"""Unit tests for the Signal PDU builder — byte-exact golden comparisons."""

from __future__ import annotations

import struct
from pathlib import Path

from gr_dis.bridge.pdu.signal import SignalState, build_signal_pdu

FIXTURES = Path(__file__).parents[1] / "fixtures" / "golden_pdus"

_STATE = SignalState(
    exercise_id=1,
    entity_site=1, entity_app=100, entity_entity=5001,
    radio_id=1,
    attached=False,
)

_SILENCE_160 = bytes([0x7F] * 160)   # μ-law silence (0 PCM → 0x7F)
_SILENCE_480 = bytes([0x7F] * 480)   # 60 ms


def _make_golden_signal(state: SignalState, ulaw_bytes: bytes) -> bytes:
    """Independently compute expected Signal PDU bytes via struct.pack."""
    n = len(ulaw_bytes)
    pad = (4 - (n % 4)) % 4
    pdu_length = 12 + 20 + n + pad
    ts = 1  # t=0 seconds past hour

    rai = (2 if state.attached else 1) << 4  # RAI at bits 5..4
    header = struct.pack(
        ">BBBBIHBB",
        7, state.exercise_id, 26, 4, ts, pdu_length, rai, 0,
    )
    fixed = struct.pack(
        ">HHHHHH IHH",
        state.entity_site, state.entity_app, state.entity_entity,
        state.radio_id,
        1,        # encoding scheme: μ-law (type 1 per SISO-REF-010 Table 176)
        0,        # TDL type
        8000,     # sample rate
        n * 8,    # data length bits
        n,
    )
    return header + fixed + ulaw_bytes + bytes(pad)


class TestSignalPDU:
    def test_length_20ms(self) -> None:
        pdu = build_signal_pdu(_STATE, _SILENCE_160, timestamp_s=0.0)
        assert len(pdu) == 192  # 12 + 20 + 160

    def test_length_60ms(self) -> None:
        pdu = build_signal_pdu(_STATE, _SILENCE_480, timestamp_s=0.0)
        assert len(pdu) == 512  # 12 + 20 + 480

    def test_golden_20ms(self) -> None:
        expected = _make_golden_signal(_STATE, _SILENCE_160)
        actual = build_signal_pdu(_STATE, _SILENCE_160, timestamp_s=0.0)
        assert actual == expected

    def test_golden_60ms(self) -> None:
        expected = _make_golden_signal(_STATE, _SILENCE_480)
        actual = build_signal_pdu(_STATE, _SILENCE_480, timestamp_s=0.0)
        assert actual == expected

    def test_pdu_type_is_26(self) -> None:
        pdu = build_signal_pdu(_STATE, _SILENCE_160, timestamp_s=0.0)
        assert pdu[2] == 26

    def test_encoding_scheme_ulaw(self) -> None:
        pdu = build_signal_pdu(_STATE, _SILENCE_160, timestamp_s=0.0)
        (encoding,) = struct.unpack_from(">H", pdu, 20)
        assert encoding == 1  # (0 << 14) | 1, G.711 μ-law per SISO-REF-010

    def test_sample_rate_8000(self) -> None:
        pdu = build_signal_pdu(_STATE, _SILENCE_160, timestamp_s=0.0)
        (sr,) = struct.unpack_from(">I", pdu, 24)
        assert sr == 8000

    def test_data_length_bits_20ms(self) -> None:
        pdu = build_signal_pdu(_STATE, _SILENCE_160, timestamp_s=0.0)
        (bits,) = struct.unpack_from(">H", pdu, 28)
        assert bits == 160 * 8

    def test_samples_count_20ms(self) -> None:
        pdu = build_signal_pdu(_STATE, _SILENCE_160, timestamp_s=0.0)
        (count,) = struct.unpack_from(">H", pdu, 30)
        assert count == 160

    def test_audio_payload_present(self) -> None:
        payload = bytes(range(256)) * 1  # 256 bytes, non-silence
        pdu = build_signal_pdu(_STATE, payload[:160], timestamp_s=0.0)
        assert pdu[32:32 + 160] == payload[:160]

    def test_padding_odd_samples(self) -> None:
        # 161 samples → pad 3 bytes → total = 12+20+161+3 = 196
        pdu = build_signal_pdu(_STATE, bytes(161), timestamp_s=0.0)
        assert len(pdu) == 196
        assert pdu[-3:] == bytes(3)  # padding is zeros

    def test_padding_already_aligned(self) -> None:
        pdu = build_signal_pdu(_STATE, bytes(160), timestamp_s=0.0)
        assert len(pdu) % 4 == 0

    def test_pdu_length_field_matches_actual(self) -> None:
        pdu = build_signal_pdu(_STATE, _SILENCE_160, timestamp_s=0.0)
        (reported,) = struct.unpack_from(">H", pdu, 8)
        assert reported == len(pdu)


class TestSignalGoldenFixtures:
    def test_write_golden_fixtures(self) -> None:
        FIXTURES.mkdir(parents=True, exist_ok=True)
        pdu_20ms = build_signal_pdu(_STATE, _SILENCE_160, timestamp_s=0.0)
        pdu_60ms = build_signal_pdu(_STATE, _SILENCE_480, timestamp_s=0.0)
        (FIXTURES / "signal_20ms.bin").write_bytes(pdu_20ms)
        (FIXTURES / "signal_60ms.bin").write_bytes(pdu_60ms)
        assert (FIXTURES / "signal_20ms.bin").stat().st_size == 192
        assert (FIXTURES / "signal_60ms.bin").stat().st_size == 512
