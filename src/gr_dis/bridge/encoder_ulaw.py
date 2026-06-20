"""G.711 μ-law encoder/decoder: int16 PCM ↔ 8-bit μ-law bytes.

Pure-Python implementation of ITU-T G.711 μ=255 companding.  audioop is NOT
used: its sign-bit convention (positive silence → 0xFF) is inverted relative to
the ITU-T standard (positive silence → 0x7F), which makes it incompatible with
the decoder table below.
"""

from __future__ import annotations

import struct

# fmt: off
# Exponent lookup table from CPython audioop.c (st_14linear2ulaw).
# Index is (bias-added magnitude >> 7) & 0xFF; value is 3-bit exponent.
_EXP_LUT: bytes = bytes([
    0,0,1,1,2,2,2,2,3,3,3,3,3,3,3,3,
    4,4,4,4,4,4,4,4,4,4,4,4,4,4,4,4,
    5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,
    5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,
    6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,
    6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,
    6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,
    6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,
    7,7,7,7,7,7,7,7,7,7,7,7,7,7,7,7,
    7,7,7,7,7,7,7,7,7,7,7,7,7,7,7,7,
    7,7,7,7,7,7,7,7,7,7,7,7,7,7,7,7,
    7,7,7,7,7,7,7,7,7,7,7,7,7,7,7,7,
    7,7,7,7,7,7,7,7,7,7,7,7,7,7,7,7,
    7,7,7,7,7,7,7,7,7,7,7,7,7,7,7,7,
    7,7,7,7,7,7,7,7,7,7,7,7,7,7,7,7,
    7,7,7,7,7,7,7,7,7,7,7,7,7,7,7,7,
])

# Lookup table for μ-law decode (from CPython audioop.c _st_ulaw2linear16, negated).
# Direct 256-element table mapping encoded u-law bytes to 16-bit linear PCM.
# Values are negated from the CPython table to match the encoder sign convention.
_ULAW2LIN_LUT: tuple[int, ...] = (
    32124,  31100,  30076,  29052,  28028,  27004,  25980,  24956,
    23932,  22908,  21884,  20860,  19836,  18812,  17788,  16764,
    15996,  15484,  14972,  14460,  13948,  13436,  12924,  12412,
    11900,  11388,  10876,  10364,  9852,  9340,  8828,  8316,
    7932,  7676,  7420,  7164,  6908,  6652,  6396,  6140,
    5884,  5628,  5372,  5116,  4860,  4604,  4348,  4092,
    3900,  3772,  3644,  3516,  3388,  3260,  3132,  3004,
    2876,  2748,  2620,  2492,  2364,  2236,  2108,  1980,
    1884,  1820,  1756,  1692,  1628,  1564,  1500,  1436,
    1372,  1308,  1244,  1180,  1116,  1052,  988,  924,
    876,  844,  812,  780,  748,  716,  684,  652,
    620,  588,  556,  524,  492,  460,  428,  396,
    372,  356,  340,  324,  308,  292,  276,  260,
    244,  228,  212,  196,  180,  164,  148,  132,
    120,  112,  104,  96,  88,  80,  72,  64,
    56,  48,  40,  32,  24,  16,  8,  0,
    -32124, -31100, -30076, -29052, -28028, -27004, -25980, -24956,
    -23932, -22908, -21884, -20860, -19836, -18812, -17788, -16764,
    -15996, -15484, -14972, -14460, -13948, -13436, -12924, -12412,
    -11900, -11388, -10876, -10364, -9852, -9340, -8828, -8316,
    -7932, -7676, -7420, -7164, -6908, -6652, -6396, -6140,
    -5884, -5628, -5372, -5116, -4860, -4604, -4348, -4092,
    -3900, -3772, -3644, -3516, -3388, -3260, -3132, -3004,
    -2876, -2748, -2620, -2492, -2364, -2236, -2108, -1980,
    -1884, -1820, -1756, -1692, -1628, -1564, -1500, -1436,
    -1372, -1308, -1244, -1180, -1116, -1052, -988, -924,
    -876, -844, -812, -780, -748, -716, -684, -652,
    -620, -588, -556, -524, -492, -460, -428, -396,
    -372, -356, -340, -324, -308, -292, -276, -260,
    -244, -228, -212, -196, -180, -164, -148, -132,
    -120, -112, -104, -96, -88, -80, -72, -64,
    -56, -48, -40, -32, -24, -16, -8, 0,
)
# fmt: on

_BIAS = 0x84   # 132 — same as CPython audioop.c
_CLIP = 32767


def _sample_to_ulaw(sample: int) -> int:
    """Encode one signed 16-bit PCM sample to μ-law; matches audioop.lin2ulaw(b,2)."""
    # audioop right-shifts by 2 to convert 16-bit → 14-bit before companding
    pcm14 = sample >> 2
    if pcm14 < 0:
        pcm_val = _BIAS - pcm14  # BIAS + |pcm14|
        sign = 0
    else:
        pcm_val = pcm14 + _BIAS
        sign = 0x80
    if pcm_val > _CLIP:
        pcm_val = _CLIP
    exp = _EXP_LUT[(pcm_val >> 7) & 0xFF]
    mantissa = (pcm_val >> (exp + 3)) & 0x0F
    return (~(sign | (exp << 4) | mantissa)) & 0xFF


def _lin2ulaw_pure(pcm_bytes: bytes) -> bytes:
    n = len(pcm_bytes) // 2
    samples = struct.unpack_from(f"<{n}h", pcm_bytes)
    return bytes(_sample_to_ulaw(s) for s in samples)


def _ulaw2lin_pure(ulaw_bytes: bytes) -> bytes:
    n = len(ulaw_bytes)
    samples = (_ULAW2LIN_LUT[b] for b in ulaw_bytes)
    return struct.pack(f"<{n}h", *samples)


lin2ulaw = _lin2ulaw_pure
ulaw2lin = _ulaw2lin_pure
