"""Integration test: inject DIS PDUs → Bridge DIS listener → ZMQ TX PUB.

No GR or SDR hardware required.  Uses real ZMQ sockets and UDP multicast
(loopback interface) to exercise the full bridge TX path.
"""

from __future__ import annotations

import asyncio
import socket
import struct
import time
from typing import Any

import msgpack
import zmq
from prometheus_client import CollectorRegistry

from gr_dis.bridge.encoder_ulaw import lin2ulaw
from gr_dis.bridge.main import run_bridge
from gr_dis.bridge.pdu.enums import (
    TRANSMIT_STATE_ON_NOT_TX,
    TRANSMIT_STATE_ON_TX,
)
from gr_dis.bridge.pdu.signal import SignalState, build_signal_pdu
from gr_dis.bridge.pdu.transmitter import TransmitterState, build_transmitter_pdu
from gr_dis.engine.config import AppConfig
from gr_dis.metrics import BridgeMetrics

# ---------------------------------------------------------------------------
# Isolated test network addresses
# ---------------------------------------------------------------------------

_ZMQ_RX_BIND = "tcp://127.0.0.1:55992"
_ZMQ_TX_BIND = "tcp://127.0.0.1:55993"
_MCAST_IP = "239.255.99.2"
_MCAST_PORT = 55002
_METRICS_BIND = "127.0.0.1:55182"
_CHANNEL_ID = "test_tx_ch1"
_RF_FREQ_HZ = 144_800_000
_EXERCISE_ID = 42

_REMOTE_SITE = 1
_REMOTE_APP = 200
_REMOTE_ENTITY = 99
_REMOTE_RADIO = 7


# ---------------------------------------------------------------------------
# Config builder
# ---------------------------------------------------------------------------


def _make_tx_config() -> AppConfig:
    raw: dict[str, Any] = {
        "dis": {
            "version": 7,
            "exercise_id": _EXERCISE_ID,
            "site_id": 1,
            "application_id": 100,
            "multicast": _MCAST_IP,
            "port": _MCAST_PORT,
            "ttl": 1,
            "loopback": True,
            "heartbeat_interval_seconds": 60.0,
        },
        "bridge": {
            "zmq_bind": _ZMQ_RX_BIND,
            "zmq_tx_bind": _ZMQ_TX_BIND,
            "metrics_bind": _METRICS_BIND,
        },
        "captures": [
            {
                "id": "cap_test",
                "zmq_tx_connect": _ZMQ_TX_BIND,
                "sdr": {
                    "driver": "rtlsdr",
                    "center_freq_hz": _RF_FREQ_HZ,
                    "sample_rate_hz": 2_400_000,
                    "gain_db": 20,
                    "duplex": "half",
                },
                "channels": [
                    {
                        "id": _CHANNEL_ID,
                        "rf_freq_hz": _RF_FREQ_HZ,
                        "bandwidth_hz": 16_000,
                        "chain": "nbfm",
                        "tx_enabled": True,
                        "radio": {
                            "radio_id": 1,
                            "entity_id": {"site": 1, "app": 100, "entity": 1},
                            "attached": False,
                            "antenna_location_ecef": [0.0, 0.0, 0.0],
                            "radio_entity_type": {
                                "kind": 7, "domain": 3, "country": 225,
                                "category": 1, "subcategory": 0, "specific": 0, "extra": 0,
                            },
                        },
                    }
                ],
            }
        ],
    }
    return AppConfig.model_validate(raw)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _send_transmitter_pdu(
    sock: socket.socket,
    transmit_state: int,
    rf_freq_hz: int = _RF_FREQ_HZ,
) -> None:
    state = TransmitterState(
        exercise_id=_EXERCISE_ID,
        entity_site=_REMOTE_SITE, entity_app=_REMOTE_APP,
        entity_entity=_REMOTE_ENTITY, radio_id=_REMOTE_RADIO,
        kind=7, domain=3, country=225, category=1,
        subcategory=0, specific=0, extra=0,
        transmit_state=transmit_state,
        rf_freq_hz=rf_freq_hz,
        bandwidth_hz=16_000.0,
        mod_major=3, mod_detail=1,
    )
    pdu = build_transmitter_pdu(state)
    sock.sendto(pdu, (_MCAST_IP, _MCAST_PORT))


def _send_signal_pdu(sock: socket.socket, ulaw_bytes: bytes) -> None:
    state = SignalState(
        exercise_id=_EXERCISE_ID,
        entity_site=_REMOTE_SITE, entity_app=_REMOTE_APP,
        entity_entity=_REMOTE_ENTITY, radio_id=_REMOTE_RADIO,
        attached=False,
    )
    pdu = build_signal_pdu(state, ulaw_bytes)
    sock.sendto(pdu, (_MCAST_IP, _MCAST_PORT))


def _drain_zmq(sub: zmq.Socket, timeout_s: float = 0.5) -> list[list[bytes]]:
    messages: list[list[bytes]] = []
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            msg = sub.recv_multipart(flags=zmq.NOBLOCK)
            messages.append(msg)
        except zmq.Again:
            time.sleep(0.01)
    return messages


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_signal_pdus_published_to_zmq_when_tx_active() -> None:
    config = _make_tx_config()
    metrics = BridgeMetrics(registry=CollectorRegistry())

    ctx = zmq.Context()
    sub = ctx.socket(zmq.SUB)
    sub.setsockopt(zmq.SUBSCRIBE, b"tx_audio.")
    sub.setsockopt(zmq.RCVTIMEO, 200)
    sub.connect(_ZMQ_TX_BIND)

    send_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    bridge_task = asyncio.create_task(run_bridge(config, metrics))
    await asyncio.sleep(0.3)

    try:
        _send_transmitter_pdu(send_sock, TRANSMIT_STATE_ON_TX)
        await asyncio.sleep(0.05)

        ulaw = lin2ulaw(struct.pack("<160h", *([1000] * 160)))
        for _ in range(5):
            _send_signal_pdu(send_sock, ulaw)
        await asyncio.sleep(0.2)

        messages = _drain_zmq(sub, timeout_s=0.3)

        assert len(messages) >= 5, f"expected ≥5 TX frames, got {len(messages)}"
        for frames in messages:
            assert frames[0] == f"tx_audio.{_CHANNEL_ID}".encode()
            hdr = msgpack.unpackb(frames[1], raw=False)
            assert hdr["type"] == "tx_audio"
            assert hdr["sr"] == 8000
            assert len(frames[2]) == 320  # 160 samples × 2 bytes

    finally:
        bridge_task.cancel()
        await asyncio.gather(bridge_task, return_exceptions=True)
        send_sock.close()
        sub.close()
        ctx.term()


async def test_signal_pdus_stop_after_transmitter_off() -> None:
    config = _make_tx_config()
    metrics = BridgeMetrics(registry=CollectorRegistry())

    ctx = zmq.Context()
    sub = ctx.socket(zmq.SUB)
    sub.setsockopt(zmq.SUBSCRIBE, b"tx_audio.")
    sub.setsockopt(zmq.RCVTIMEO, 100)
    sub.connect(_ZMQ_TX_BIND)

    send_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    bridge_task = asyncio.create_task(run_bridge(config, metrics))
    await asyncio.sleep(0.3)

    try:
        ulaw = lin2ulaw(struct.pack("<160h", *([500] * 160)))

        _send_transmitter_pdu(send_sock, TRANSMIT_STATE_ON_TX)
        await asyncio.sleep(0.05)
        _send_signal_pdu(send_sock, ulaw)
        await asyncio.sleep(0.05)
        _send_transmitter_pdu(send_sock, TRANSMIT_STATE_ON_NOT_TX)
        await asyncio.sleep(0.05)

        _drain_zmq(sub, timeout_s=0.2)

        for _ in range(3):
            _send_signal_pdu(send_sock, ulaw)
        await asyncio.sleep(0.2)

        after_messages = _drain_zmq(sub, timeout_s=0.2)
        assert len(after_messages) == 0, (
            f"expected 0 TX frames after transmitter-off, got {len(after_messages)}"
        )

    finally:
        bridge_task.cancel()
        await asyncio.gather(bridge_task, return_exceptions=True)
        send_sock.close()
        sub.close()
        ctx.term()


async def test_wrong_exercise_id_dropped() -> None:
    config = _make_tx_config()
    metrics = BridgeMetrics(registry=CollectorRegistry())

    ctx = zmq.Context()
    sub = ctx.socket(zmq.SUB)
    sub.setsockopt(zmq.SUBSCRIBE, b"tx_audio.")
    sub.setsockopt(zmq.RCVTIMEO, 100)
    sub.connect(_ZMQ_TX_BIND)

    send_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    bridge_task = asyncio.create_task(run_bridge(config, metrics))
    await asyncio.sleep(0.3)

    try:
        wrong_state = TransmitterState(
            exercise_id=0,  # wrong — not _EXERCISE_ID=42
            entity_site=_REMOTE_SITE, entity_app=_REMOTE_APP,
            entity_entity=_REMOTE_ENTITY, radio_id=_REMOTE_RADIO,
            kind=7, domain=3, country=225, category=1,
            subcategory=0, specific=0, extra=0,
            transmit_state=TRANSMIT_STATE_ON_TX,
            rf_freq_hz=_RF_FREQ_HZ, bandwidth_hz=16_000.0,
            mod_major=3, mod_detail=1,
        )
        send_sock.sendto(build_transmitter_pdu(wrong_state), (_MCAST_IP, _MCAST_PORT))
        await asyncio.sleep(0.05)

        ulaw = lin2ulaw(struct.pack("<160h", *([100] * 160)))
        _send_signal_pdu(send_sock, ulaw)
        await asyncio.sleep(0.2)

        messages = _drain_zmq(sub, timeout_s=0.2)
        assert len(messages) == 0

    finally:
        bridge_task.cancel()
        await asyncio.gather(bridge_task, return_exceptions=True)
        send_sock.close()
        sub.close()
        ctx.term()
