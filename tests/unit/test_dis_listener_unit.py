"""Unit tests for DIS listener routing logic."""

from __future__ import annotations

import asyncio
import contextlib
from unittest.mock import MagicMock, patch

from prometheus_client import CollectorRegistry

from gr_dis.bridge.dis_listener import _handle_signal, _handle_transmitter, run_dis_listener
from gr_dis.bridge.pdu.enums import (
    TRANSMIT_STATE_ON_NOT_TX,
    TRANSMIT_STATE_ON_TX,
)
from gr_dis.bridge.pdu.signal import SignalState, build_signal_pdu
from gr_dis.bridge.pdu.transmitter import TransmitterState, build_transmitter_pdu
from gr_dis.bridge.tx_channel import TxChannelState
from gr_dis.metrics import BridgeMetrics


def _make_metrics() -> BridgeMetrics:
    return BridgeMetrics(registry=CollectorRegistry())


def _make_channel(**kwargs: object) -> TxChannelState:
    defaults: dict = dict(
        channel_id="ch0",
        rf_freq_hz=144_800_000,
        bandwidth_hz=16_000,
        authorized=True,
        accepted_mod_keys={(3, 1)},
        tx_filter=None,
    )
    defaults.update(kwargs)
    return TxChannelState(**defaults)  # type: ignore[arg-type]


def _tx_pdu(
    transmit_state: int = TRANSMIT_STATE_ON_TX,
    rf_freq_hz: int = 144_800_000,
    mod_major: int = 3,
    mod_detail: int = 1,
) -> bytes:
    state = TransmitterState(
        exercise_id=1,
        entity_site=1, entity_app=200, entity_entity=42, radio_id=5,
        kind=7, domain=3, country=225, category=1, subcategory=0, specific=0, extra=0,
        transmit_state=transmit_state,
        rf_freq_hz=rf_freq_hz,
        bandwidth_hz=16_000.0,
        mod_major=mod_major, mod_detail=mod_detail,
    )
    return build_transmitter_pdu(state)


def _sig_pdu(ulaw_bytes: bytes = b"\xff" * 160) -> bytes:
    state = SignalState(
        exercise_id=1,
        entity_site=1, entity_app=200, entity_entity=42, radio_id=5,
        attached=False,
    )
    return build_signal_pdu(state, ulaw_bytes)


def test_transmitter_on_tx_acquires_lock() -> None:
    ch = _make_channel()
    channels = {"ch0": ch}
    metrics = _make_metrics()
    _handle_transmitter(_tx_pdu(TRANSMIT_STATE_ON_TX), channels, metrics)
    assert ch.active_holder == (1, 200, 42, 5)


def test_transmitter_on_not_tx_releases_lock() -> None:
    ch = _make_channel()
    channels = {"ch0": ch}
    metrics = _make_metrics()
    ch.active_holder = (1, 200, 42, 5)
    _handle_transmitter(_tx_pdu(TRANSMIT_STATE_ON_NOT_TX), channels, metrics)
    assert ch.active_holder is None


def test_transmitter_modulation_mismatch_drops() -> None:
    ch = _make_channel()  # accepted_mod_keys={(3, 1)} — FM/Angle
    channels = {"ch0": ch}
    metrics = _make_metrics()
    # AM modulation (1, 1) not in accepted_mod_keys
    _handle_transmitter(_tx_pdu(TRANSMIT_STATE_ON_TX, mod_major=1, mod_detail=1), channels, metrics)
    assert ch.active_holder is None  # lock NOT acquired


def test_transmitter_no_matching_channel_ignored() -> None:
    ch = _make_channel(rf_freq_hz=144_800_000, bandwidth_hz=16_000)
    channels = {"ch0": ch}
    metrics = _make_metrics()
    # PDU at 430 MHz — no matching channel
    _handle_transmitter(_tx_pdu(TRANSMIT_STATE_ON_TX, rf_freq_hz=430_000_000), channels, metrics)
    assert ch.active_holder is None


def test_signal_publishes_pcm_when_holder_found() -> None:
    ch = _make_channel()
    ch.active_holder = (1, 200, 42, 5)
    channels = {"ch0": ch}
    metrics = _make_metrics()
    publisher = MagicMock()
    ulaw = b"\x7f" * 160
    _handle_signal(_sig_pdu(ulaw), channels, publisher, metrics)
    publisher.publish.assert_called_once()
    call_channel, call_pcm = publisher.publish.call_args[0]
    assert call_channel == "ch0"
    assert len(call_pcm) == 320  # 160 μ-law → 160 × int16


def test_signal_dropped_when_no_holder() -> None:
    ch = _make_channel()
    # No active holder
    channels = {"ch0": ch}
    metrics = _make_metrics()
    publisher = MagicMock()
    _handle_signal(_sig_pdu(), channels, publisher, metrics)
    publisher.publish.assert_not_called()


def test_signal_dropped_when_unauthorized() -> None:
    ch = _make_channel(authorized=False)
    ch.active_holder = (1, 200, 42, 5)
    channels = {"ch0": ch}
    metrics = _make_metrics()
    publisher = MagicMock()
    _handle_signal(_sig_pdu(), channels, publisher, metrics)
    publisher.publish.assert_not_called()


def test_queue_full_increments_rx_pdu_queue_drops() -> None:
    """QueueFull in _reader must increment rx_pdu_queue_drops, not parse errors."""
    metrics = _make_metrics()

    sock = MagicMock()
    sock.fileno.return_value = 99
    sock.recvfrom.side_effect = [
        (b"\x00" * 32, ("127.0.0.1", 3000)),
        BlockingIOError,
    ]

    queue_mock: MagicMock = MagicMock(spec=asyncio.Queue)
    queue_mock.put_nowait.side_effect = asyncio.QueueFull

    # Capture the _reader callback registered via loop.add_reader
    captured_reader: list = []

    def fake_add_reader(fd: int, callback: object) -> None:
        captured_reader.append(callback)

    # Patch make_listener_socket at the point of use (imported into dis_listener)
    with (
        patch("gr_dis.bridge.dis_listener.make_listener_socket", return_value=sock),
        patch("asyncio.Queue", return_value=queue_mock),
    ):
        async def _run() -> None:
            loop = asyncio.get_running_loop()
            # queue.get() must return an awaitable so run_dis_listener can start
            future: asyncio.Future[bytes] = loop.create_future()
            queue_mock.get = MagicMock(return_value=future)

            with patch.object(loop, "add_reader", side_effect=fake_add_reader):
                task = asyncio.create_task(
                    run_dis_listener(
                        multicast_ip="239.0.0.1",
                        port=3000,
                        exercise_id=1,
                        channels={},
                        publisher=MagicMock(),
                        metrics=metrics,
                    )
                )
                # Yield control so run_dis_listener reaches add_reader and queue.get
                await asyncio.sleep(0)
                assert captured_reader, "add_reader was not called"
                captured_reader[0]()  # invoke _reader directly — triggers QueueFull
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task

        asyncio.run(_run())

    assert metrics.rx_pdu_queue_drops._value.get() == 1
    assert metrics.rx_pdu_parse_errors._value.get() == 0
