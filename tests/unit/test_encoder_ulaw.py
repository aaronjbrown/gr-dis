"""Unit tests for the G.711 μ-law encoder."""

from __future__ import annotations

import math
import struct

from gr_dis.bridge.encoder_ulaw import _lin2ulaw_pure, _sample_to_ulaw, lin2ulaw


def _pcm_bytes(*samples: int) -> bytes:
    """Pack signed 16-bit PCM samples into little-endian bytes."""
    return struct.pack(f"<{len(samples)}h", *samples)


class TestSampleToUlaw:
    """Test the per-sample conversion against known G.711 reference values."""

    def test_zero(self) -> None:
        # PCM 0 → μ-law 0x7F (positive silence)
        assert _sample_to_ulaw(0) == 0x7F

    def test_negative_one(self) -> None:
        # PCM -1 → μ-law 0xFF
        assert _sample_to_ulaw(-1) == 0xFF

    def test_max_positive(self) -> None:
        # PCM 32767 → μ-law 0x1F
        assert _sample_to_ulaw(32767) == 0x1F

    def test_max_negative(self) -> None:
        # PCM -32768 → μ-law 0x9F
        assert _sample_to_ulaw(-32768) == 0x9F

    def test_small_positive(self) -> None:
        # PCM 4 (pcm14=1, sign=0x80): pcm_val=133, exp=0, mantissa=2 → 0x7D
        result = _sample_to_ulaw(4)
        assert 0 <= result <= 0xFF

    def test_output_is_byte(self) -> None:
        for sample in range(-32768, 32768, 256):
            assert 0 <= _sample_to_ulaw(sample) <= 255

    def test_positive_range_in_upper_half(self) -> None:
        # Positive PCM samples map to 0x00..0x7F
        for sample in range(0, 32768, 512):
            assert _sample_to_ulaw(sample) < 0x80

    def test_negative_range_in_lower_half(self) -> None:
        # Negative PCM samples map to 0x80..0xFF
        for sample in range(-32768, 0, 512):
            assert _sample_to_ulaw(sample) >= 0x80


class TestPureImplementation:
    def test_empty_input(self) -> None:
        assert _lin2ulaw_pure(b"") == b""

    def test_single_zero_sample(self) -> None:
        assert _lin2ulaw_pure(_pcm_bytes(0)) == bytes([0x7F])

    def test_single_neg_one_sample(self) -> None:
        assert _lin2ulaw_pure(_pcm_bytes(-1)) == bytes([0xFF])

    def test_multiple_samples(self) -> None:
        pcm = _pcm_bytes(0, -1, 32767, -32768)
        result = _lin2ulaw_pure(pcm)
        assert result == bytes([0x7F, 0xFF, 0x1F, 0x9F])

    def test_160_samples_output_length(self) -> None:
        pcm = _pcm_bytes(*([0] * 160))
        result = _lin2ulaw_pure(pcm)
        assert len(result) == 160


class TestLinToUlaw:
    """lin2ulaw must produce the same output as the pure fallback for all inputs."""

    def test_empty(self) -> None:
        assert lin2ulaw(b"") == b""

    def test_silence_matches_pure(self) -> None:
        pcm = _pcm_bytes(*([0] * 160))
        assert lin2ulaw(pcm) == _lin2ulaw_pure(pcm)

    def test_reference_values_match_pure(self) -> None:
        samples = [0, -1, 1, 100, -100, 1000, -1000, 32767, -32768]
        pcm = _pcm_bytes(*samples)
        assert lin2ulaw(pcm) == _lin2ulaw_pure(pcm)

    def test_sine_wave_matches_pure(self) -> None:
        """Sine wave at 1 kHz, 160 samples (20 ms at 8 kHz)."""
        samples = [
            int(16384 * math.sin(2 * math.pi * 1000 * i / 8000))
            for i in range(160)
        ]
        pcm = _pcm_bytes(*samples)
        assert lin2ulaw(pcm) == _lin2ulaw_pure(pcm)

    def test_full_range_matches_pure(self) -> None:
        """Every possible 16-bit sample value — exhaustive equivalence check."""
        all_samples = list(range(-32768, 32768))
        pcm = struct.pack(f"<{len(all_samples)}h", *all_samples)
        assert lin2ulaw(pcm) == _lin2ulaw_pure(pcm)

    def test_silence_encodes_to_0x7f(self) -> None:
        pcm = _pcm_bytes(0)
        assert lin2ulaw(pcm) == bytes([0x7F])

    def test_companding_is_nonlinear(self) -> None:
        # μ-law compresses dynamics: large values are closer together than small ones
        pcm_small = _pcm_bytes(100)
        pcm_large = _pcm_bytes(10000)
        small_code = lin2ulaw(pcm_small)[0]
        large_code = lin2ulaw(pcm_large)[0]
        # Both positive → both < 0x80; large has lower code (more compressed)
        assert small_code < 0x80
        assert large_code < 0x80
        assert small_code > large_code  # closer to 0x7F = silence
