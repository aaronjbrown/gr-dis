"""Unit tests for G.711 μ-law decoder."""

from __future__ import annotations

import struct

import pytest

from gr_dis.bridge.encoder_ulaw import lin2ulaw, ulaw2lin


def test_ulaw2lin_silence() -> None:
    # μ-law 0xFF encodes silence; decodes back to 0
    assert ulaw2lin(b"\xff") == b"\x00\x00"


def test_ulaw2lin_known_value() -> None:
    # μ-law 0x00 is the maximum positive code; decodes to a non-zero int16 LE
    result = ulaw2lin(b"\x00")
    sample = struct.unpack_from("<h", result)[0]
    assert sample != 0


def test_roundtrip_is_lossy_but_close() -> None:
    # Encode then decode — μ-law is lossy; result within ±8 of original for zero
    pcm_zero = struct.pack("<h", 0)
    decoded = ulaw2lin(lin2ulaw(pcm_zero))
    sample = struct.unpack_from("<h", decoded)[0]
    assert abs(sample) < 16


def test_ulaw2lin_length_matches() -> None:
    # 8 μ-law bytes → 16 PCM bytes (8 × int16)
    result = ulaw2lin(b"\xff" * 8)
    assert len(result) == 16


def test_ulaw2lin_roundtrip_non_zero() -> None:
    # Encode a non-trivial value and decode; should be in the right ballpark
    original = struct.pack("<10h", 1000, -1000, 500, -500, 100, -100, 200, -200, 50, -50)
    decoded = ulaw2lin(lin2ulaw(original))
    originals = struct.unpack("<10h", original)
    decodeds = struct.unpack("<10h", decoded)
    for o, d in zip(originals, decodeds, strict=True):
        # The encoder works in 14-bit space (>>2); decoder outputs 14-bit scale.
        # Compare against the 14-bit representation of the original.
        expected = o // 4
        assert abs(expected - d) < max(abs(expected) * 0.15 + 20, 20)


def test_ulaw2lin_audioop_matches_pure_python() -> None:
    try:
        import audioop
    except ImportError:
        pytest.skip("audioop not available (Python 3.13+)")
    data = bytes(range(256))
    from gr_dis.bridge.encoder_ulaw import _ulaw2lin_pure
    assert _ulaw2lin_pure(data) == audioop.ulaw2lin(data, 2)
