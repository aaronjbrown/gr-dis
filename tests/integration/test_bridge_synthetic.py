"""Integration test: synthetic ZMQ publisher → Bridge → multicast UDP PDUs.

Exercises the full bridge pipeline end-to-end using SyntheticPublisher as the
GR-Capture stand-in.  No real hardware or GNURadio install is required.
"""

from __future__ import annotations

import asyncio
import urllib.request
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import socket

import pytest
from prometheus_client import CollectorRegistry

from gr_dis.bridge.main import run_bridge
from gr_dis.bridge.multicast import make_listener_socket
from gr_dis.bridge.pdu.enums import (
    PDU_TYPE_SIGNAL,
    PDU_TYPE_TRANSMITTER,
    TRANSMIT_STATE_OFF,
    TRANSMIT_STATE_ON_NOT_TX,
    TRANSMIT_STATE_ON_TX,
)
from gr_dis.engine.config import AppConfig
from gr_dis.metrics import BridgeMetrics
from tests.integration._synthetic_publisher import SyntheticPublisher

# ---------------------------------------------------------------------------
# Test-specific network addresses  (isolated from the example-config defaults)
# ---------------------------------------------------------------------------

_ZMQ_BIND = "tcp://127.0.0.1:55988"
_MCAST_IP = "239.255.99.1"
_MCAST_PORT = 55001
_METRICS_BIND = "127.0.0.1:55180"
_CHANNEL_ID = "test_ch1"
_RF_FREQ_HZ = 100_000_000  # 100 MHz — matches the test SDR center freq

# ---------------------------------------------------------------------------
# Config builder
# ---------------------------------------------------------------------------


def _make_test_config() -> AppConfig:
    raw: dict = {
        "dis": {
            "version": 7,
            "exercise_id": 1,
            "site_id": 1,
            "application_id": 100,
            "multicast": _MCAST_IP,
            "port": _MCAST_PORT,
            "ttl": 1,
            "loopback": True,
            "heartbeat_interval_seconds": 0.5,
        },
        "bridge": {
            "zmq_bind": _ZMQ_BIND,
            "metrics_bind": _METRICS_BIND,
        },
        "captures": [
            {
                "id": "cap_test",
                "sdr": {
                    "driver": "rtlsdr",
                    "center_freq_hz": _RF_FREQ_HZ,
                    "sample_rate_hz": 2_400_000,
                    "gain_db": 20,
                },
                "channels": [
                    {
                        "id": _CHANNEL_ID,
                        "rf_freq_hz": _RF_FREQ_HZ,
                        "bandwidth_hz": 25_000,
                        "chain": "nbfm",
                        "radio": {
                            "radio_id": 1,
                            "entity_id": {"site": 1, "app": 100, "entity": 1},
                            "attached": False,
                            "antenna_location_ecef": [0.0, 0.0, 0.0],
                            "radio_entity_type": {
                                "kind": 7,
                                "domain": 3,
                                "country": 1,
                                "category": 1,
                                "subcategory": 0,
                                "specific": 0,
                                "extra": 0,
                            },
                        },
                    }
                ],
            }
        ],
    }
    return AppConfig.model_validate(raw)


# ---------------------------------------------------------------------------
# PDU parsing helpers
# ---------------------------------------------------------------------------


def _drain_socket(sock: socket.socket) -> list[bytes]:
    """Read all available datagrams from a non-blocking socket."""
    pdus: list[bytes] = []
    while True:
        try:
            data, _ = sock.recvfrom(65535)
            pdus.append(data)
        except BlockingIOError:
            break
    return pdus


def _pdu_type(pdu: bytes) -> int:
    return pdu[2]


def _transmit_state(pdu: bytes) -> int:
    # Transmitter PDU body layout after the 12-byte common header:
    #   HHHH    entity (site, app, entity, radio_id)  = 8 bytes  (offsets 12–19)
    #   BBHBBBB radio entity type                     = 8 bytes  (offsets 20–27)
    #   B       transmit_state                                    (offset 28)
    return pdu[28]


def _check_nonzero_counter(body: str, metric_name: str) -> None:
    for line in body.splitlines():
        is_data = line.startswith(metric_name) and not line.startswith("#")
        if is_data and float(line.split()[-1]) > 0:
            return
    pytest.fail(f"No non-zero value found for metric {metric_name!r}")


# ---------------------------------------------------------------------------
# Test
# ---------------------------------------------------------------------------


async def test_bridge_voice_scenario() -> None:
    """Bridge emits correct DIS PDUs in response to a synthetic voice event."""
    config = _make_test_config()
    metrics = BridgeMetrics(registry=CollectorRegistry())

    # Open the listener before starting the bridge so startup PDUs are captured.
    listener = make_listener_socket(_MCAST_IP, _MCAST_PORT)
    bridge_task = asyncio.create_task(run_bridge(config, metrics=metrics))

    try:
        # Allow bridge to start, bind its ZMQ socket, and emit startup PDUs.
        await asyncio.sleep(0.3)

        # Run the voice scenario: meta → idle → squelch_open → voice → squelch_close → idle.
        async with SyntheticPublisher(_ZMQ_BIND, [_CHANNEL_ID]) as pub:
            await pub.run_voice_scenario(
                _CHANNEL_ID,
                rf_freq_hz=_RF_FREQ_HZ,
                n_idle_before=3,
                n_voice=8,
                n_idle_after=3,
            )

        # Wait for heartbeat PDUs at 0.5 s interval (expect at least two).
        await asyncio.sleep(1.2)
        pdus = _drain_socket(listener)

        # --- Metrics endpoint ---
        with urllib.request.urlopen(f"http://{_METRICS_BIND}/metrics", timeout=3) as resp:
            metrics_body = resp.read().decode()
        _check_nonzero_counter(metrics_body, "gr_dis_signal_pdus_sent_total")
        _check_nonzero_counter(metrics_body, "gr_dis_transmitter_pdus_sent_total")

        # --- PDU type assertions ---
        pdu_types = [_pdu_type(p) for p in pdus]
        assert PDU_TYPE_TRANSMITTER in pdu_types, (
            f"No Transmitter PDU received; PDU types seen: {sorted(set(pdu_types))}"
        )
        assert PDU_TYPE_SIGNAL in pdu_types, (
            f"No Signal PDU received; PDU types seen: {sorted(set(pdu_types))}"
        )

        # --- Transmit-state transitions ---
        tx_states = [
            _transmit_state(p) for p in pdus if _pdu_type(p) == PDU_TYPE_TRANSMITTER
        ]
        assert TRANSMIT_STATE_ON_TX in tx_states, (
            f"ON_TX state never observed in Transmitter PDUs; states: {tx_states}"
        )
        assert TRANSMIT_STATE_ON_NOT_TX in tx_states, (
            f"ON_NOT_TX state never observed in Transmitter PDUs; states: {tx_states}"
        )

        # --- Graceful shutdown: Off PDU emitted, task finishes within 2 s ---
        bridge_task.cancel()
        done, _ = await asyncio.wait([bridge_task], timeout=2.0)
        assert bridge_task in done, "Bridge did not shut down within 2 seconds"

        # Poll the listener for up to 1 s so kernel-level multicast delivery
        # has time to land in our socket buffer after the bridge sends the
        # Off PDUs and exits.
        shutdown_pdus: list[bytes] = []
        for _ in range(20):
            await asyncio.sleep(0.05)
            shutdown_pdus.extend(_drain_socket(listener))
            if any(
                _pdu_type(p) == PDU_TYPE_TRANSMITTER and _transmit_state(p) == TRANSMIT_STATE_OFF
                for p in shutdown_pdus
            ):
                break
        tx_shutdown = [p for p in shutdown_pdus if _pdu_type(p) == PDU_TYPE_TRANSMITTER]
        assert tx_shutdown, "No Transmitter PDU received during bridge shutdown"
        assert any(_transmit_state(p) == TRANSMIT_STATE_OFF for p in tx_shutdown), (
            "No Off Transmitter PDU received during bridge shutdown; "
            f"states seen: {[_transmit_state(p) for p in tx_shutdown]}"
        )

    finally:
        if not bridge_task.done():
            bridge_task.cancel()
            await asyncio.gather(bridge_task, return_exceptions=True)
        listener.close()
