"""Unit tests for the Transmitter PDU builder — byte-exact golden comparisons."""

from __future__ import annotations

import struct
from pathlib import Path

import pytest

from gr_dis.bridge.pdu.enums import (
    TRANSMIT_STATE_ON_NOT_TX,
    TRANSMIT_STATE_ON_TX,
)
from gr_dis.bridge.pdu.transmitter import TransmitterState, build_transmitter_pdu

FIXTURES = Path(__file__).parents[1] / "fixtures" / "golden_pdus"

# ---------------------------------------------------------------------------
# Reference state objects
# ---------------------------------------------------------------------------

_STANDALONE = TransmitterState(
    exercise_id=1,
    entity_site=1, entity_app=100, entity_entity=5001,
    radio_id=1,
    kind=7, domain=3, country=225,
    category=1, subcategory=0, specific=0, extra=0,
    transmit_state=TRANSMIT_STATE_ON_NOT_TX,
    input_source=1,
    antenna_location_ecef=(3_875_000.0, 332_000.0, 5_025_000.0),
    relative_antenna_location=(0.0, 0.0, 0.0),
    rf_freq_hz=144_800_000,
    bandwidth_hz=25_000.0,
    power_dbm=0.0,
    attached=False,
)

_ATTACHED = TransmitterState(
    exercise_id=1,
    entity_site=1, entity_app=100, entity_entity=12,
    radio_id=2,
    kind=7, domain=2, country=225,
    category=1, subcategory=0, specific=0, extra=0,
    transmit_state=TRANSMIT_STATE_ON_TX,
    input_source=1,
    antenna_location_ecef=(3_875_010.0, 332_005.0, 5_025_020.0),
    relative_antenna_location=(1.5, 0.0, 2.2),
    rf_freq_hz=146_400_000,
    bandwidth_hz=25_000.0,
    power_dbm=5.0,
    attached=True,
)


def _make_golden_transmitter(state: TransmitterState) -> bytes:
    """Independently compute expected Transmitter PDU bytes via struct.pack."""
    # Use timestamp = 1 (t=0 seconds past hour, absolute bit set)
    ts = 1

    # Header
    rai = (2 if state.attached else 1) << 4  # RAI at bits 5..4
    header = struct.pack(
        ">BBBBIHBB",
        7,                # protocol version
        state.exercise_id,
        25,               # PDU type: Transmitter
        4,                # protocol family: Radio Communications
        ts,
        104,              # PDU length
        rai,
        0,                # padding
    )

    x, y, z = state.antenna_location_ecef
    xr, yr, zr = state.relative_antenna_location

    body = struct.pack(
        ">HHHH"
        "BBHBBBB"
        "BBH"
        "ddd"
        "fff"
        "HH"
        "Q"
        "ff"
        "HHHH"
        "HH"
        "B3x",
        state.entity_site, state.entity_app, state.entity_entity,
        state.radio_id,
        state.kind, state.domain, state.country,
        state.category, state.subcategory, state.specific, state.extra,
        state.transmit_state, state.input_source,
        0,          # VarTxParamsCount
        x, y, z,
        xr, yr, zr,
        0, 0,       # antenna pattern type + length
        state.rf_freq_hz,
        state.bandwidth_hz, state.power_dbm,
        state.mod_spread, state.mod_major, state.mod_detail, state.mod_system,
        0, 0,       # crypto
        0,          # mod param length
    )
    return header + body


class TestTransmitterPDU:
    def test_length_standalone(self) -> None:
        pdu = build_transmitter_pdu(_STANDALONE, timestamp_s=0.0)
        assert len(pdu) == 104

    def test_length_attached(self) -> None:
        pdu = build_transmitter_pdu(_ATTACHED, timestamp_s=0.0)
        assert len(pdu) == 104

    def test_golden_standalone(self) -> None:
        expected = _make_golden_transmitter(_STANDALONE)
        actual = build_transmitter_pdu(_STANDALONE, timestamp_s=0.0)
        assert actual == expected

    def test_golden_attached(self) -> None:
        expected = _make_golden_transmitter(_ATTACHED)
        actual = build_transmitter_pdu(_ATTACHED, timestamp_s=0.0)
        assert actual == expected

    def test_field_pdu_type_is_25(self) -> None:
        pdu = build_transmitter_pdu(_STANDALONE, timestamp_s=0.0)
        assert pdu[2] == 25

    def test_field_protocol_version_7(self) -> None:
        pdu = build_transmitter_pdu(_STANDALONE, timestamp_s=0.0)
        assert pdu[0] == 7

    def test_field_protocol_family_4(self) -> None:
        pdu = build_transmitter_pdu(_STANDALONE, timestamp_s=0.0)
        assert pdu[3] == 4

    def test_field_rai_unattached(self) -> None:
        pdu = build_transmitter_pdu(_STANDALONE, timestamp_s=0.0)
        assert pdu[10] == 0x10  # RAI=1 << 4 (bits 5..4)

    def test_field_rai_attached(self) -> None:
        pdu = build_transmitter_pdu(_ATTACHED, timestamp_s=0.0)
        assert pdu[10] == 0x20  # RAI=2 << 4 (bits 5..4)

    def test_field_transmit_state_standalone(self) -> None:
        pdu = build_transmitter_pdu(_STANDALONE, timestamp_s=0.0)
        assert pdu[28] == TRANSMIT_STATE_ON_NOT_TX

    def test_field_transmit_state_attached(self) -> None:
        pdu = build_transmitter_pdu(_ATTACHED, timestamp_s=0.0)
        assert pdu[28] == TRANSMIT_STATE_ON_TX

    def test_field_radio_id(self) -> None:
        pdu = build_transmitter_pdu(_STANDALONE, timestamp_s=0.0)
        (radio_id,) = struct.unpack_from(">H", pdu, 18)
        assert radio_id == 1

    def test_field_rf_frequency(self) -> None:
        pdu = build_transmitter_pdu(_STANDALONE, timestamp_s=0.0)
        (freq,) = struct.unpack_from(">Q", pdu, 72)
        assert freq == 144_800_000

    def test_field_modulation_type(self) -> None:
        pdu = build_transmitter_pdu(_STANDALONE, timestamp_s=0.0)
        spread, major, detail, system = struct.unpack_from(">HHHH", pdu, 88)
        assert spread == 0
        assert major == 3   # Angle / FM
        assert detail == 1  # FM (Angle)
        assert system == 1  # Generic

    def test_field_crypto_zero(self) -> None:
        pdu = build_transmitter_pdu(_STANDALONE, timestamp_s=0.0)
        crypto_sys, crypto_key = struct.unpack_from(">HH", pdu, 96)
        assert crypto_sys == 0
        assert crypto_key == 0

    def test_field_entity_id(self) -> None:
        pdu = build_transmitter_pdu(_STANDALONE, timestamp_s=0.0)
        site, app, entity = struct.unpack_from(">HHH", pdu, 12)
        assert site == 1
        assert app == 100
        assert entity == 5001

    def test_field_antenna_ecef(self) -> None:
        pdu = build_transmitter_pdu(_STANDALONE, timestamp_s=0.0)
        x, y, z = struct.unpack_from(">ddd", pdu, 32)
        assert x == pytest.approx(3_875_000.0)
        assert y == pytest.approx(332_000.0)
        assert z == pytest.approx(5_025_000.0)

    def test_field_relative_antenna_attached(self) -> None:
        pdu = build_transmitter_pdu(_ATTACHED, timestamp_s=0.0)
        xr, yr, zr = struct.unpack_from(">fff", pdu, 56)
        assert xr == pytest.approx(1.5, rel=1e-5)
        assert yr == pytest.approx(0.0, abs=1e-6)
        assert zr == pytest.approx(2.2, rel=1e-5)


class TestGoldenFixtures:
    """Write golden .bin files so tshark can validate them externally."""

    @pytest.fixture(autouse=True)
    def write_fixtures(self) -> None:
        FIXTURES.mkdir(parents=True, exist_ok=True)
        standalone = build_transmitter_pdu(_STANDALONE, timestamp_s=0.0)
        attached = build_transmitter_pdu(_ATTACHED, timestamp_s=0.0)
        (FIXTURES / "transmitter_standalone.bin").write_bytes(standalone)
        (FIXTURES / "transmitter_attached.bin").write_bytes(attached)

    def test_fixture_files_written(self) -> None:
        assert (FIXTURES / "transmitter_standalone.bin").exists()
        assert (FIXTURES / "transmitter_attached.bin").exists()
