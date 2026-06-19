"""Bridge async entrypoint: wires subscriber → encoder → PDU builders → multicast."""

from __future__ import annotations

import asyncio
import logging
import signal
import threading
from typing import TYPE_CHECKING

from gr_dis.bridge.dis_listener import run_dis_listener
from gr_dis.bridge.multicast import make_multicast_socket
from gr_dis.bridge.pdu.emission import derive_emission_designator
from gr_dis.bridge.pdu.enums import CHAIN_MODULATION
from gr_dis.bridge.radio_state import RadioChannelHandler
from gr_dis.bridge.subscriber import run_subscriber
from gr_dis.bridge.tx_channel import TxChannelState
from gr_dis.bridge.tx_publisher import TxPublisher
from gr_dis.metrics import BridgeMetrics, start_metrics_server

if TYPE_CHECKING:
    from gr_dis.engine.config import AppConfig

logger = logging.getLogger(__name__)

_HEARTBEAT_RETRY_BUDGET = 3


def _build_tx_channels(config: AppConfig) -> dict[str, TxChannelState]:
    """Build TxChannelState for every tx_enabled channel; log authorization status."""
    import yaml  # noqa: PLC0415

    from gr_dis.engine.config import BandPlanRange  # noqa: PLC0415

    auth = config.rf_tx_authorization
    states: dict[str, TxChannelState] = {}

    for capture in config.captures:
        for ch in capture.channels:
            if not ch.tx_enabled:
                continue

            chain_mod = CHAIN_MODULATION.get(ch.chain)
            if chain_mod:
                _, mod_major, mod_detail, _ = chain_mod
                accepted_mod_keys: set[tuple[int, int]] = {(mod_major, mod_detail)}
            else:
                mod_major = mod_detail = 0
                accepted_mod_keys = set()

            authorized = True
            if auth is not None:
                ranges = list(auth.authorized_ranges)
                if auth.band_plan_file:
                    try:
                        with open(auth.band_plan_file) as f:
                            bp = yaml.safe_load(f)
                        ranges.extend(
                            BandPlanRange.model_validate(r)
                            for r in bp.get("ranges", [])
                        )
                    except Exception as exc:
                        logger.warning("failed to load band plan %s: %s", auth.band_plan_file, exc)

                designator = derive_emission_designator(
                    mod_major, mod_detail, float(ch.bandwidth_hz)
                )
                authorized = False
                for r in ranges:
                    if r.from_hz <= ch.rf_freq_hz <= r.to_hz and (
                        r.emission_designators is None
                        or (designator and designator in r.emission_designators)
                    ):
                        authorized = True
                        break

                if not authorized:
                    logger.warning(
                        "channel %s: TX not authorized (freq=%d, designator=%s) — "
                        "add to rf_tx_authorization.authorized_ranges to enable",
                        ch.id, ch.rf_freq_hz, designator,
                    )

            states[ch.id] = TxChannelState(
                channel_id=ch.id,
                rf_freq_hz=ch.rf_freq_hz,
                bandwidth_hz=ch.bandwidth_hz,
                authorized=authorized,
                accepted_mod_keys=accepted_mod_keys,
                tx_filter=ch.tx_filter,
            )

    return states


async def _guarded_heartbeat(handler: RadioChannelHandler, metrics: BridgeMetrics) -> None:
    """Run handler.heartbeat_loop(), restarting up to _HEARTBEAT_RETRY_BUDGET times.

    After the budget is exhausted, marks the channel dead so /healthz returns 503.
    """
    for attempt in range(_HEARTBEAT_RETRY_BUDGET + 1):
        try:
            await handler.heartbeat_loop()
            return
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning(
                "heartbeat exception for %s (attempt %d/%d)",
                handler.channel_id, attempt + 1, _HEARTBEAT_RETRY_BUDGET + 1,
            )
            if attempt < _HEARTBEAT_RETRY_BUDGET:
                await asyncio.sleep(1.0 * (attempt + 1))
            else:
                logger.error(
                    "heartbeat for %s exhausted retry budget; marking channel dead",
                    handler.channel_id,
                )
                metrics.mark_channel_dead(handler.channel_id)


async def run_bridge(config: AppConfig, metrics: BridgeMetrics | None = None) -> None:
    """Run the bridge until the current task is cancelled or SIGTERM received.

    Args:
        config: fully validated AppConfig.
        metrics: optional pre-built BridgeMetrics (mainly for testing with an
                 isolated CollectorRegistry).  Defaults to a new instance that
                 registers against the global Prometheus registry.
    """
    if metrics is None:
        metrics = BridgeMetrics()

    # --- Multicast socket ---
    mcast_sock = make_multicast_socket(config.dis)

    # --- Build per-channel handlers ---
    handlers: dict[str, RadioChannelHandler] = {}
    for capture in config.captures:
        for channel in capture.channels:
            handlers[channel.id] = RadioChannelHandler(
                channel, config.dis, mcast_sock, metrics
            )

    # --- Metrics HTTP server ---
    try:
        metrics_server = start_metrics_server(metrics, config.bridge.metrics_bind)
    except OSError as exc:
        logger.warning("could not start metrics server: %s", exc)
        metrics_server = None

    # --- Emit startup Transmitter PDUs ---
    for handler in handlers.values():
        handler.send_transmitter_pdu()
        logger.info("startup Transmitter PDU sent for channel %s", handler.channel_id)

    # --- Heartbeat tasks ---
    heartbeat_tasks = [
        asyncio.create_task(
            _guarded_heartbeat(h, metrics), name=f"heartbeat/{h.channel_id}"
        )
        for h in handlers.values()
    ]

    # --- TX path (if any channels have tx_enabled: true) ---
    tx_channels = _build_tx_channels(config)
    tx_publisher: TxPublisher | None = None
    listener_task = None
    if tx_channels:
        assert config.bridge.zmq_tx_bind is not None  # validated by AppConfig
        tx_publisher = TxPublisher(config.bridge.zmq_tx_bind)
        listener_task = asyncio.create_task(
            run_dis_listener(
                config.dis.multicast,
                config.dis.port,
                config.dis.exercise_id,
                tx_channels,
                tx_publisher,
                metrics,
            ),
            name="dis_listener",
        )
        logger.info(
            "DIS listener started: %d TX channel(s), ZMQ TX PUB on %s",
            len(tx_channels), config.bridge.zmq_tx_bind,
        )

    # --- SIGTERM → cancel ---
    # asyncio's signal handlers only attach to the main thread of the main
    # interpreter. When run_bridge() is driven from a worker thread (e.g. the
    # CLI's ``gr-dis run`` co-hosts GR + bridge in one process), we skip the
    # handler — the caller is expected to cancel the bridge task at shutdown.
    loop = asyncio.get_running_loop()
    sigterm_handler_installed = False

    def _handle_sigterm() -> None:
        logger.info("SIGTERM received; shutting down bridge")
        for task in asyncio.all_tasks(loop):
            task.cancel()

    if threading.current_thread() is threading.main_thread():
        try:
            loop.add_signal_handler(signal.SIGTERM, _handle_sigterm)
            sigterm_handler_installed = True
        except (NotImplementedError, RuntimeError):
            logger.debug("SIGTERM handler not installable on this loop/thread")

    # --- Main subscriber loop ---
    sub_task = asyncio.create_task(
        run_subscriber(config.bridge.zmq_bind, handlers),
        name="zmq_subscriber",
    )

    logger.info(
        "bridge running: %d channel(s), ZMQ SUB on %s, multicast → %s:%d",
        len(handlers),
        config.bridge.zmq_bind,
        config.dis.multicast,
        config.dis.port,
    )

    try:
        all_tasks = [sub_task, *heartbeat_tasks]
        if listener_task is not None:
            all_tasks.append(listener_task)
        await asyncio.gather(*all_tasks)
    except asyncio.CancelledError:
        logger.info("bridge cancelled; draining")
    finally:
        sub_task.cancel()
        for t in heartbeat_tasks:
            t.cancel()
        if listener_task is not None:
            listener_task.cancel()
        drain = [sub_task, *heartbeat_tasks]
        if listener_task is not None:
            drain.append(listener_task)
        await asyncio.gather(*drain, return_exceptions=True)
        for handler in handlers.values():
            handler.send_off_transmitter_pdu()
            logger.info("Off Transmitter PDU sent for channel %s", handler.channel_id)
        if tx_publisher is not None:
            tx_publisher.close()
        mcast_sock.close()
        if metrics_server is not None:
            metrics_server.shutdown()
        if sigterm_handler_installed:
            loop.remove_signal_handler(signal.SIGTERM)
        logger.info("bridge shut down cleanly")
