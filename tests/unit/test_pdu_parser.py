"""Unit tests for the incoming DIS PDU parser."""

from __future__ import annotations

import pytest

from gr_dis.bridge.pdu.enums import (
    PDU_TYPE_SIGNAL,
    PDU_TYPE_TRANSMITTER,
    TRANSMIT_STATE_ON_TX,
)
from gr_dis.bridge.pdu.parser import (
    ParseError,
    parse_header,
    parse_signal_pdu,
    parse_transmitter_pdu,
)
from gr_dis.bridge.pdu.signal import SignalState, build_signal_pdu
from gr_dis.bridge.pdu.transmitter import TransmitterState, build_transmitter_pdu


def _make_tx_state(**kwargs: object) -> TransmitterState:
    defaults: dict = dict(
        exercise_id=5,
        entity_site=1, entity_app=100, entity_entity=42, radio_id=3,
        kind=7, domain=3, country=225, category=1,
        subcategory=0, specific=0, extra=0,
        transmit_state=TRANSMIT_STATE_ON_TX,
        rf_freq_hz=144_800_000,
        bandwidth_hz=16_000.0,
        mod_major=2, mod_detail=5,
    )
    defaults.update(kwargs)
    return TransmitterState(**defaults)  # type: ignore[arg-type]


def test_parse_header_pdu_type() -> None:
    pdu = build_transmitter_pdu(_make_tx_state())
    hdr = parse_header(pdu)
    assert hdr.pdu_type == PDU_TYPE_TRANSMITTER
    assert hdr.exercise_id == 5


def test_parse_header_signal() -> None:
    state = SignalState(exercise_id=3, entity_site=1, entity_app=100,
                        entity_entity=1, radio_id=1, attached=False)
    pdu = build_signal_pdu(state, b"\xff" * 160)
    hdr = parse_header(pdu)
    assert hdr.pdu_type == PDU_TYPE_SIGNAL
    assert hdr.exercise_id == 3


def test_parse_transmitter_round_trip() -> None:
    state = _make_tx_state()
    pdu = build_transmitter_pdu(state)
    fields = parse_transmitter_pdu(pdu)
    assert fields.entity_site == 1
    assert fields.entity_app == 100
    assert fields.entity_entity == 42
    assert fields.radio_id == 3
    assert fields.transmit_state == TRANSMIT_STATE_ON_TX
    assert fields.rf_freq_hz == 144_800_000
    assert abs(fields.bandwidth_hz - 16_000.0) < 1
    assert fields.mod_major == 2
    assert fields.mod_detail == 5


def test_parse_signal_round_trip() -> None:
    ulaw = bytes(range(256)) * 1  # 256 bytes of μ-law
    state = SignalState(exercise_id=7, entity_site=2, entity_app=200,
                        entity_entity=99, radio_id=4, attached=False)
    pdu = build_signal_pdu(state, ulaw)
    fields = parse_signal_pdu(pdu)
    assert fields.entity_site == 2
    assert fields.entity_app == 200
    assert fields.entity_entity == 99
    assert fields.radio_id == 4
    assert fields.encoding_scheme == 1  # ENCODING_SCHEME_ULAW_8K
    assert fields.sample_rate == 8000
    assert fields.n_samples == 256
    assert fields.audio_data == ulaw


def test_parse_header_too_short_raises() -> None:
    with pytest.raises(ParseError):
        parse_header(b"\x00" * 6)


def test_parse_transmitter_too_short_raises() -> None:
    with pytest.raises(ParseError):
        parse_transmitter_pdu(b"\x00" * 50)


def test_parse_signal_too_short_raises() -> None:
    with pytest.raises(ParseError):
        parse_signal_pdu(b"\x00" * 20)


def test_parse_signal_truncated_audio_raises() -> None:
    """n_samples field larger than actual trailing payload must raise ParseError."""
    state = SignalState(exercise_id=1, entity_site=1, entity_app=1,
                        entity_entity=1, radio_id=1, attached=False)
    pdu = build_signal_pdu(state, b"\xff" * 160)
    # Chop the last 10 bytes so the audio payload is shorter than n_samples claims.
    truncated = pdu[:-10]
    with pytest.raises(ParseError, match="truncated"):
        parse_signal_pdu(truncated)
