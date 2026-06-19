"""Transmitter PDU builder (IEEE 1278.1-2012, PDU Type 25)."""

from __future__ import annotations

import struct
from dataclasses import dataclass, field

from gr_dis.bridge.pdu.enums import (
    MOD_DETAIL_FM,
    MOD_MAJOR_ANALOG,
    MOD_SPREAD_SPECTRUM,
    MOD_SYSTEM_GENERIC,
    PDU_TYPE_TRANSMITTER,
    TRANSMIT_STATE_ON_NOT_TX,
)
from gr_dis.bridge.pdu.header import pack_header
from gr_dis.bridge.pdu.timestamp import dis_timestamp

_TRANSMITTER_LENGTH = 104

# Body layout (92 bytes after the 12-byte header):
#   HHH H         entity_id (site, app, entity) + radio_id      = 8 bytes
#   BBHBBBB       radio_entity_type                              = 8 bytes
#   BB H          transmit_state, input_source, var_tx_count=0  = 4 bytes
#   ddd           antenna ECEF (x, y, z)                        = 24 bytes
#   fff           relative antenna (x, y, z)                    = 12 bytes
#   HH            antenna_pattern_type=0, antenna_pattern_len=0  = 4 bytes
#   Q             rf_freq_hz                                     = 8 bytes
#   ff            bandwidth_hz, power_dbm                        = 8 bytes
#   HHHH          modulation_type (spread, major, detail, system)= 8 bytes
#   HH            crypto_system=0, crypto_key_id=0              = 4 bytes
#   B 3x          mod_param_length=0 + 3 padding                = 4 bytes
#   ─────────────────────────────────────────────────────────── = 92 bytes
_BODY_FMT = (
    ">HHHH"    # entity site, app, entity + radio_id
    "BBHBBBB"  # radio entity type
    "BBH"      # transmit_state, input_source, var_tx_params_count
    "ddd"      # antenna ECEF
    "fff"      # relative antenna
    "HH"       # antenna_pattern_type, antenna_pattern_len
    "Q"        # rf_freq_hz
    "ff"       # bandwidth_hz, power_dbm
    "HHHH"     # modulation spread, major, detail, system
    "HH"       # crypto_system, crypto_key_id
    "B3x"      # mod_param_length + 3 pad bytes
)
assert struct.calcsize(_BODY_FMT) == 92


@dataclass
class TransmitterState:
    # PDU context
    exercise_id: int
    # Entity / Radio identity
    entity_site: int
    entity_app: int
    entity_entity: int
    radio_id: int
    # Radio Entity Type (IEEE 1278.1-2012 §6.2.72)
    kind: int
    domain: int
    country: int
    category: int
    subcategory: int
    specific: int
    extra: int
    # Transmitter state
    transmit_state: int = TRANSMIT_STATE_ON_NOT_TX
    input_source: int = 1  # 1 = Pilot
    # Location (ECEF metres)
    antenna_location_ecef: tuple[float, float, float] = field(
        default_factory=lambda: (0.0, 0.0, 0.0)
    )
    # Body-relative metres; zeros for standalone
    relative_antenna_location: tuple[float, float, float] = field(
        default_factory=lambda: (0.0, 0.0, 0.0)
    )
    # RF parameters
    rf_freq_hz: int = 0
    bandwidth_hz: float = 0.0
    power_dbm: float = 0.0
    # Whether this radio is attached to a parent entity (sets RAI flag)
    attached: bool = False
    # Modulation Type subfields (defaults: NBFM)
    mod_spread: int = MOD_SPREAD_SPECTRUM
    mod_major: int = MOD_MAJOR_ANALOG
    mod_detail: int = MOD_DETAIL_FM
    mod_system: int = MOD_SYSTEM_GENERIC


def build_transmitter_pdu(
    state: TransmitterState,
    *,
    timestamp_s: float | None = None,
) -> bytes:
    """Build a 104-byte DIS v7 Transmitter PDU from ``state``."""
    ts = dis_timestamp(timestamp_s)
    header = pack_header(
        PDU_TYPE_TRANSMITTER,
        state.exercise_id,
        _TRANSMITTER_LENGTH,
        state.attached,
        ts,
    )
    x, y, z = state.antenna_location_ecef
    xr, yr, zr = state.relative_antenna_location
    body = struct.pack(
        _BODY_FMT,
        state.entity_site, state.entity_app, state.entity_entity,
        state.radio_id,
        state.kind, state.domain, state.country,
        state.category, state.subcategory, state.specific, state.extra,
        state.transmit_state,
        state.input_source,
        0,          # VarTxParamsCount
        x, y, z,   # ECEF
        xr, yr, zr, # relative antenna
        0, 0,       # antenna pattern type + length
        state.rf_freq_hz,
        state.bandwidth_hz, state.power_dbm,
        state.mod_spread, state.mod_major, state.mod_detail, state.mod_system,
        0, 0,       # crypto system + key
        0,          # mod param length
    )
    return header + body
