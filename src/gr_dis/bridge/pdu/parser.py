"""Minimal incoming DIS PDU parser — extracts only routing and audio fields."""

from __future__ import annotations

import struct
from dataclasses import dataclass


class ParseError(ValueError):
    """Raised when a PDU is too short or structurally invalid."""


@dataclass
class PduHeader:
    pdu_type: int
    exercise_id: int
    pdu_length: int
    pdu_status: int


@dataclass
class TransmitterFields:
    entity_site: int
    entity_app: int
    entity_entity: int
    radio_id: int
    transmit_state: int
    rf_freq_hz: int
    bandwidth_hz: float
    mod_major: int
    mod_detail: int


@dataclass
class SignalFields:
    entity_site: int
    entity_app: int
    entity_entity: int
    radio_id: int
    encoding_scheme: int
    sample_rate: int
    n_samples: int
    audio_data: bytes


# Header: version(B), exercise_id(B), pdu_type(B), family(B),
#         timestamp(I), pdu_length(H), pdu_status(B), pad(B)
_HEADER_FMT = ">BBBBIHBB"
_HEADER_SIZE = 12
_TX_MIN_SIZE = 104
# Signal PDU: 12-byte header + 20-byte fixed body = 32 bytes minimum
_SIG_MIN_SIZE = 32


def parse_header(data: bytes) -> PduHeader:
    """Parse the 12-byte common PDU header."""
    if len(data) < _HEADER_SIZE:
        raise ParseError(f"PDU too short for header: {len(data)} < {_HEADER_SIZE}")
    try:
        _, exercise_id, pdu_type, _, _, pdu_length, pdu_status, _ = struct.unpack_from(
            _HEADER_FMT, data, 0
        )
    except struct.error as exc:
        raise ParseError(str(exc)) from exc
    return PduHeader(
        pdu_type=pdu_type,
        exercise_id=exercise_id,
        pdu_length=pdu_length,
        pdu_status=pdu_status,
    )


def parse_transmitter_pdu(data: bytes) -> TransmitterFields:
    """Parse routing and RF fields from a 104-byte Transmitter PDU."""
    if len(data) < _TX_MIN_SIZE:
        raise ParseError(f"Transmitter PDU too short: {len(data)} < {_TX_MIN_SIZE}")
    try:
        # Offsets after the 12-byte header:
        # +0  entity_site  H (2)  → absolute 12
        # +2  entity_app   H (2)  → absolute 14
        # +4  entity_entity H (2) → absolute 16
        # +6  radio_id     H (2)  → absolute 18
        site, app, entity, radio_id = struct.unpack_from(">HHHH", data, 12)

        # +16 transmit_state B (1) → absolute 28
        # (kind B, domain B, country H, category B, subcategory B, specific B, extra B
        #  = 1+1+2+1+1+1+1 = 8 bytes at offsets 20-27)
        (transmit_state,) = struct.unpack_from(">B", data, 28)

        # +30 input_source B (1) → absolute 29  (skipped)
        # +31 var_tx_params_count H (2) → absolute 30-31 (skipped)
        # +32 ECEF ddd (24) → absolute 32-55 (skipped)
        # +56 relative fff (12) → absolute 56-67 (skipped)
        # +68 antenna_pattern_type H (2) → absolute 68-69 (skipped)
        # +70 antenna_pattern_len H (2) → absolute 70-71 (skipped)
        # +72 rf_freq_hz Q (8) → absolute 72
        (rf_freq_hz,) = struct.unpack_from(">Q", data, 72)

        # +80 bandwidth_hz f (4) → absolute 80
        (bandwidth_hz,) = struct.unpack_from(">f", data, 80)

        # +84 power_dbm f (4) → absolute 84 (skipped)
        # +88 mod_spread H (2) → absolute 88 (skipped)
        # +90 mod_major H (2) → absolute 90
        (mod_major,) = struct.unpack_from(">H", data, 90)

        # +92 mod_detail H (2) → absolute 92
        (mod_detail,) = struct.unpack_from(">H", data, 92)
    except struct.error as exc:
        raise ParseError(str(exc)) from exc
    return TransmitterFields(
        entity_site=site,
        entity_app=app,
        entity_entity=entity,
        radio_id=radio_id,
        transmit_state=transmit_state,
        rf_freq_hz=rf_freq_hz,
        bandwidth_hz=bandwidth_hz,
        mod_major=mod_major,
        mod_detail=mod_detail,
    )


def parse_signal_pdu(data: bytes) -> SignalFields:
    """Parse routing and audio fields from a Signal PDU."""
    if len(data) < _SIG_MIN_SIZE:
        raise ParseError(f"Signal PDU too short: {len(data)} < {_SIG_MIN_SIZE}")
    try:
        # Fixed body layout (offsets relative to start of data):
        # 12  entity_site     H (2)
        # 14  entity_app      H (2)
        # 16  entity_entity   H (2)
        # 18  radio_id        H (2)
        # 20  encoding_scheme H (2)
        # 22  tdl_type        H (2)  — skipped
        # 24  sample_rate     I (4)
        # 28  data_length_bits H (2) — skipped
        # 30  n_samples       H (2)
        # 32+ audio payload
        site, app, entity, radio_id = struct.unpack_from(">HHHH", data, 12)
        (encoding_scheme,) = struct.unpack_from(">H", data, 20)
        (sample_rate,) = struct.unpack_from(">I", data, 24)
        (n_samples,) = struct.unpack_from(">H", data, 30)
    except struct.error as exc:
        raise ParseError(str(exc)) from exc
    audio_data = data[32: 32 + n_samples]
    if len(audio_data) != n_samples:
        raise ParseError(
            f"Signal PDU audio truncated: got {len(audio_data)} bytes, expected {n_samples}"
        )
    return SignalFields(
        entity_site=site,
        entity_app=app,
        entity_entity=entity,
        radio_id=radio_id,
        encoding_scheme=encoding_scheme,
        sample_rate=sample_rate,
        n_samples=n_samples,
        audio_data=audio_data,
    )
