"""DIS protocol constants for PDU construction."""

# PDU Types (IEEE 1278.1-2012 §6.2.66)
PDU_TYPE_TRANSMITTER = 25
PDU_TYPE_SIGNAL = 26

# Protocol family (§6.2.65)
PROTOCOL_FAMILY_RADIO = 4

# Protocol version
PROTOCOL_VERSION = 7

# Signal PDU encoding scheme: class=0 (Encoded Audio), type=1 (8-bit μ-law G.711)
# Per SISO-REF-010 Table 176: type 1 = G.711 μ-law; type 4 = 16-bit linear PCM.
# Encoding Scheme = (class << 14) | type  →  (0 << 14) | 1 = 1
ENCODING_SCHEME_ULAW_8K: int = 1
SIGNAL_SAMPLE_RATE = 8000

# Modulation Type subfields (§6.2.59)
MOD_SPREAD_SPECTRUM = 0
MOD_SYSTEM_GENERIC = 1

# Major Modulation values
MOD_MAJOR_AMPLITUDE_AND_ANGLE = 2  # "Amplitude and Angle" per IEEE 1278.1-2012
MOD_MAJOR_ANGLE = 3                # Pure angle modulation (e.g. WFM broadcast FM)

# Detail values under Major = Amplitude and Angle (Analog Detail enumeration)
MOD_DETAIL_FM_ANALOG = 5   # FM entry in the Analog detail table

# Detail values under Major = Angle (Angle Detail enumeration)
MOD_DETAIL_FM_ANGLE = 1    # FM entry in the Angle detail table

# Aliases used as defaults for TransmitterState (FM/Angle — correct for any voice FM radio)
MOD_MAJOR_ANALOG = MOD_MAJOR_ANGLE
MOD_DETAIL_FM = MOD_DETAIL_FM_ANGLE

# Chain name → (spread, major, detail, system) per IEEE 1278.1-2012 §6.2.59
# Both NBFM and WFM are pure angle modulation (major=3); bandwidth field distinguishes them.
_S = MOD_SPREAD_SPECTRUM
_G = MOD_SYSTEM_GENERIC
CHAIN_MODULATION: dict[str, tuple[int, int, int, int]] = {
    "nbfm": (_S, MOD_MAJOR_ANGLE, MOD_DETAIL_FM_ANGLE, _G),
    "wfm":  (_S, MOD_MAJOR_ANGLE, MOD_DETAIL_FM_ANGLE, _G),
}

# Transmit State (§6.2.93)
TRANSMIT_STATE_OFF = 0
TRANSMIT_STATE_ON_NOT_TX = 1
TRANSMIT_STATE_ON_TX = 2

# Radio Attached Indicator values placed in bits 4..3 of PDU Status (§6.2.67)
RAI_UNATTACHED = 1
RAI_ATTACHED = 2
