"""Signal PDU builder (IEEE 1278.1-2012, PDU Type 26)."""

from __future__ import annotations

import struct
from dataclasses import dataclass

from gr_dis.bridge.pdu.enums import (
    ENCODING_SCHEME_ULAW_8K,
    PDU_TYPE_SIGNAL,
    SIGNAL_SAMPLE_RATE,
)
from gr_dis.bridge.pdu.header import pack_header
from gr_dis.bridge.pdu.timestamp import dis_timestamp

# Fixed body fields before the audio payload (20 bytes):
#   HHH H   entity_id (site, app, entity) + radio_id  = 8 bytes
#   H H     encoding_scheme, tdl_type                 = 4 bytes
#   I       sample_rate                               = 4 bytes
#   H H     data_length_bits, samples                 = 4 bytes
#   ─────────────────────────────────────────────────  = 20 bytes
_FIXED_FMT = ">HHHHHH IHH"
assert struct.calcsize(_FIXED_FMT) == 20

_HEADER_SIZE = 12
_FIXED_SIZE = 20


@dataclass
class SignalState:
    exercise_id: int
    entity_site: int
    entity_app: int
    entity_entity: int
    radio_id: int
    attached: bool


def build_signal_pdu(
    state: SignalState,
    ulaw_bytes: bytes,
    *,
    timestamp_s: float | None = None,
) -> bytes:
    """Build a DIS v7 Signal PDU carrying ``ulaw_bytes`` of μ-law audio.

    The payload is padded to the next 4-byte boundary as required by the spec.
    """
    n_samples = len(ulaw_bytes)
    pad = (4 - (n_samples % 4)) % 4
    pdu_length = _HEADER_SIZE + _FIXED_SIZE + n_samples + pad

    ts = dis_timestamp(timestamp_s)
    header = pack_header(
        PDU_TYPE_SIGNAL,
        state.exercise_id,
        pdu_length,
        state.attached,
        ts,
    )
    fixed = struct.pack(
        _FIXED_FMT,
        state.entity_site, state.entity_app, state.entity_entity,
        state.radio_id,
        ENCODING_SCHEME_ULAW_8K,
        0,                    # TDL type = Other
        SIGNAL_SAMPLE_RATE,   # 8000 Hz
        n_samples * 8,        # data length in bits
        n_samples,
    )
    return header + fixed + ulaw_bytes + bytes(pad)
