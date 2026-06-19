#!/usr/bin/env python3
"""Multi-channel stress and latency validator.

Spawns the bridge in-process, drives N synthetic channels for the specified
duration, prints periodic progress, and exits 0 on PASS / 1 on FAIL / 2 on
bridge crash.

Usage:
    .venv/bin/python scripts/stress-multichannel.py [options]

Defaults: 32 channels for 600 seconds.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging
import sys
import traceback
from pathlib import Path

# Make the project importable when run as a script.
_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "tests"))  # for `tests.integration._stress_*`

from prometheus_client import CollectorRegistry  # noqa: E402
from tests.integration._stress_publisher import (  # noqa: E402
    ChannelSpec,
    MultiChannelStressPublisher,
)
from tests.integration._stress_reporting import (  # noqa: E402
    build_report,
    format_report,
    parse_prometheus,
    scrape,
)

from gr_dis.bridge.main import run_bridge  # noqa: E402
from gr_dis.engine.config import AppConfig  # noqa: E402
from gr_dis.metrics import BridgeMetrics  # noqa: E402

_CENTER_FREQ_HZ = 100_000_000
_SAMPLE_RATE_HZ = 2_400_000


def _build_config(args: argparse.Namespace) -> AppConfig:
    channels: list[dict] = []
    for i in range(args.channels):
        rf = _CENTER_FREQ_HZ - 400_000 + i * 25_000
        channels.append(
            {
                "id": f"stress_ch_{i}",
                "rf_freq_hz": rf,
                "bandwidth_hz": 25_000,
                "chain": "nbfm",
                "radio": {
                    "radio_id": 1,
                    "entity_id": {"site": 1, "app": 100, "entity": 5000 + i},
                    "attached": False,
                    "antenna_location_ecef": [0.0, 0.0, 0.0],
                    "radio_entity_type": {
                        "kind": 7, "domain": 3, "country": 1,
                        "category": 1, "subcategory": 0,
                        "specific": 0, "extra": 0,
                    },
                },
            }
        )
    raw = {
        "dis": {
            "version": 7,
            "exercise_id": 1,
            "site_id": 1,
            "application_id": 100,
            "multicast": args.mcast_ip,
            "port": args.mcast_port,
            "ttl": 1,
            "loopback": True,
            "heartbeat_interval_seconds": 5.0,
        },
        "bridge": {
            "zmq_bind": args.zmq_bind,
            "metrics_bind": args.metrics_bind,
        },
        "captures": [
            {
                "id": "cap_stress",
                "sdr": {
                    "driver": "rtlsdr",
                    "center_freq_hz": _CENTER_FREQ_HZ,
                    "sample_rate_hz": _SAMPLE_RATE_HZ,
                    "gain_db": 20,
                },
                "channels": channels,
            }
        ],
    }
    return AppConfig.model_validate(raw)


async def _progress_loop(
    metrics_url: str,
    interval_s: float,
    deadline: float,
    n_channels: int,
) -> None:
    """Print one-line progress every interval_s seconds until deadline."""
    start = asyncio.get_event_loop().time()
    while asyncio.get_event_loop().time() < deadline:
        await asyncio.sleep(interval_s)
        if asyncio.get_event_loop().time() >= deadline:
            return
        try:
            parsed = parse_prometheus(scrape(metrics_url))
        except Exception:  # noqa: BLE001
            print("progress: /metrics scrape failed (continuing)")
            continue
        sig = parsed.get("gr_dis_signal_pdus_sent_total", 0.0)
        hwm = parsed.get("gr_dis_zmq_hwm_drops_total", 0.0)
        dropped = parsed.get("gr_dis_audio_frames_dropped_total", 0.0)
        hist = parsed.get("gr_dis_e2e_latency_seconds")
        if isinstance(hist, dict) and hist.get("count", 0) > 0:
            count = float(hist["count"])  # type: ignore[arg-type]
            buckets = hist["buckets"]  # type: ignore[index]
            p99 = float(buckets.get(0.5, 0.0)) / count * 100.0  # type: ignore[union-attr]
        else:
            p99 = 0.0
        elapsed = asyncio.get_event_loop().time() - start
        print(
            f"t={elapsed:6.1f}s  channels={n_channels}  "
            f"sig_pdus={int(sig):8d}  drops={int(dropped)}  "
            f"zmq_hwm_drops={int(hwm)}  p99_le_500ms={p99:5.1f}%"
        )


async def _amain(args: argparse.Namespace) -> int:
    config = _build_config(args)
    metrics = BridgeMetrics(registry=CollectorRegistry())
    bridge_task = asyncio.create_task(run_bridge(config, metrics=metrics))

    try:
        await asyncio.sleep(0.5)
        if bridge_task.done():
            exc = bridge_task.exception()
            if exc is not None:
                print("Bridge failed during startup:", file=sys.stderr)
                traceback.print_exception(exc, file=sys.stderr)
                return 2
        metrics_url = f"http://{args.metrics_bind}/metrics"
        before = scrape(metrics_url)

        specs = [
            ChannelSpec(
                channel_id=f"stress_ch_{i}",
                rf_freq_hz=_CENTER_FREQ_HZ - 400_000 + i * 25_000,
            )
            for i in range(args.channels)
        ]

        loop = asyncio.get_event_loop()
        run_deadline = loop.time() + args.duration
        progress_task = asyncio.create_task(
            _progress_loop(metrics_url, args.report_interval, run_deadline, args.channels)
        )

        async with MultiChannelStressPublisher(
            zmq_connect=args.zmq_bind,
            channel_specs=specs,
            duty_cycle=args.duty_cycle,
            rng_seed=args.seed,
        ) as pub:
            await pub.run(duration_s=args.duration)

        progress_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await progress_task

        # Bridge drain.
        await asyncio.sleep(0.5)
        after = scrape(metrics_url)
        report = build_report(
            before, after,
            duration_s=args.duration,
            n_channels=args.channels,
        )

        print()
        print(format_report(report))
        return 0 if report.overall_pass else 1

    except Exception:  # noqa: BLE001
        traceback.print_exc()
        return 2
    finally:
        bridge_task.cancel()
        try:
            await asyncio.wait_for(bridge_task, timeout=2.0)
        except (TimeoutError, asyncio.TimeoutError):
            print("WARNING: bridge did not shut down in 2 s", file=sys.stderr)
        except asyncio.CancelledError:
            pass


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Multi-channel stress and latency validator",
    )
    parser.add_argument("--channels", type=int, default=32)
    parser.add_argument("--duration", type=float, default=600.0)
    parser.add_argument("--duty-cycle", type=float, default=0.25)
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--report-interval", type=float, default=10.0)
    parser.add_argument("--zmq-bind", default="tcp://127.0.0.1:55991")
    parser.add_argument("--metrics-bind", default="127.0.0.1:55182")
    parser.add_argument("--mcast-ip", default="239.255.99.3")
    parser.add_argument("--mcast-port", type=int, default=55003)
    parser.add_argument("--log-level", default="WARNING")
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.WARNING),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    return asyncio.run(_amain(args))


if __name__ == "__main__":
    sys.exit(main())
