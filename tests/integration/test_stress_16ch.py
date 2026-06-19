"""Regression gate: 16 channels, 10 seconds, must PASS.

Mid-scale check between test_stress_smoke.py (8 ch × 5 s) and
test_stress_32ch.py (32 ch × 30 s, marked slow). Confirms the bridge sustains
NFR-1 latency and zero drops at the half-scale point.
"""

from __future__ import annotations

import asyncio

from prometheus_client import CollectorRegistry

from gr_dis.bridge.main import run_bridge
from gr_dis.engine.config import AppConfig
from gr_dis.metrics import BridgeMetrics
from tests.integration._stress_publisher import (
    ChannelSpec,
    MultiChannelStressPublisher,
)
from tests.integration._stress_reporting import (
    build_report,
    format_report,
    scrape,
)

# Ports disjoint from existing stress smoke + bridge synthetic tests.
_ZMQ_BIND = "tcp://127.0.0.1:55889"
_MCAST_IP = "239.255.99.4"
_MCAST_PORT = 55004
_METRICS_BIND = "127.0.0.1:55183"
_METRICS_URL = f"http://{_METRICS_BIND}/metrics"

_CENTER_FREQ_HZ = 100_000_000
_SAMPLE_RATE_HZ = 2_400_000
_N_CHANNELS = 16
_DURATION_S = 10.0


def _make_config(n_channels: int) -> AppConfig:
    channels: list[dict[str, object]] = []
    for i in range(n_channels):
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
            "multicast": _MCAST_IP,
            "port": _MCAST_PORT,
            "ttl": 1,
            "loopback": True,
            "heartbeat_interval_seconds": 5.0,
        },
        "bridge": {
            "zmq_bind": _ZMQ_BIND,
            "metrics_bind": _METRICS_BIND,
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


async def test_stress_16ch_10s() -> None:
    config = _make_config(_N_CHANNELS)
    metrics = BridgeMetrics(registry=CollectorRegistry())
    bridge_task = asyncio.create_task(run_bridge(config, metrics=metrics))

    try:
        await asyncio.sleep(0.3)
        before = scrape(_METRICS_URL)

        specs = [
            ChannelSpec(
                channel_id=f"stress_ch_{i}",
                rf_freq_hz=_CENTER_FREQ_HZ - 400_000 + i * 25_000,
            )
            for i in range(_N_CHANNELS)
        ]
        async with MultiChannelStressPublisher(
            zmq_connect=_ZMQ_BIND,
            channel_specs=specs,
            duty_cycle=0.5,
            rng_seed=2,
        ) as pub:
            await pub.run(duration_s=_DURATION_S)

        await asyncio.sleep(0.3)
        after = scrape(_METRICS_URL)

        report = build_report(
            before, after,
            duration_s=_DURATION_S,
            n_channels=_N_CHANNELS,
        )

        assert report.has_samples, (
            f"No latency samples observed.\n{format_report(report)}"
        )
        assert report.drops_pass, (
            f"Frames dropped or HWM drops detected.\n{format_report(report)}"
        )
        assert report.median_pass, (
            f"Median latency over 250 ms.\n{format_report(report)}"
        )
        assert report.p99_pass, (
            f"p99 latency over 500 ms.\n{format_report(report)}"
        )
        assert report.signal_pdus_total >= _N_CHANNELS, (
            f"Expected >= {_N_CHANNELS} Signal PDUs, got "
            f"{report.signal_pdus_total}.\n{format_report(report)}"
        )
    finally:
        bridge_task.cancel()
        done, _pending = await asyncio.wait([bridge_task], timeout=2.0)
        assert bridge_task in done, "Bridge did not shut down within 2 seconds"
