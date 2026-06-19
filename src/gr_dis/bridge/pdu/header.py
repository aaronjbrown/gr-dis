"""Common 12-byte DIS PDU header (IEEE 1278.1-2012 §5.2.3)."""

from __future__ import annotations

import struct

from gr_dis.bridge.pdu.enums import (
    PROTOCOL_FAMILY_RADIO,
    PROTOCOL_VERSION,
    RAI_ATTACHED,
    RAI_UNATTACHED,
)

_HEADER_FMT = ">BBBBIHBB"  # 4+4+2+1+1 = 12 bytes
_HEADER_SIZE = struct.calcsize(_HEADER_FMT)
assert _HEADER_SIZE == 12


def pdu_status(attached: bool) -> int:
    """PDU Status byte: RAI field in bits 5..4 (IEEE 1278.1-2012 §6.2.67)."""
    rai = RAI_ATTACHED if attached else RAI_UNATTACHED
    return rai << 4


def pack_header(
    pdu_type: int,
    exercise_id: int,
    pdu_length: int,
    attached: bool,
    timestamp: int,
) -> bytes:
    """Pack the 12-byte common PDU header."""
    return struct.pack(
        _HEADER_FMT,
        PROTOCOL_VERSION,  # offset 0
        exercise_id,       # offset 1
        pdu_type,          # offset 2
        PROTOCOL_FAMILY_RADIO,  # offset 3
        timestamp,         # offset 4-7
        pdu_length,        # offset 8-9
        pdu_status(attached),   # offset 10
        0,                 # offset 11 (padding)
    )
