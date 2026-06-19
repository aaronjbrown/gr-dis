#!/usr/bin/env python3
"""Synthesize an 8-carrier NBFM IQ fixture for multi-channel stress testing.

Generates ``tests/fixtures/recorded_iq/nbfm_multichannel.cf32``: 10 seconds of
complex-float-32 baseband at 2.4 MHz centered on 100 MHz, containing 8 NBFM
carriers at -400…+400 kHz offsets (100 kHz spacing) with staggered 1-second
talk windows.

Channel i:
  * rf_freq = 100 MHz + (i - 3.5) * 100 kHz   (skips offset 0)
  * talk window:  t = (i + 1) … (i + 2) s
  * audio tone:   (400 + 200·i) Hz, NBFM-modulated at ±5 kHz deviation

Together with ``examples/config_stress_8ch.yaml`` this fixture lets:

    toolbox run --container gr-dis gr-dis run \\
        --config examples/config_stress_8ch.yaml \\
        --source-file tests/fixtures/recorded_iq/nbfm_multichannel.cf32

emit 8 distinct radios' worth of Signal PDUs that an operator can verify
chronologically (channel 0 talks first, channel 7 last).

The fixture is gitignored (``tests/fixtures/recorded_iq/*.cf32``); regenerate
any time the carrier layout changes.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

# --- Global parameters -------------------------------------------------------
SDR_CENTER_HZ = 100_000_000.0
SDR_SAMPLE_RATE_HZ = 2_400_000.0
DURATION_S = 10.0
MAX_DEVIATION_HZ = 5_000.0
CARRIER_AMP = 0.9
NOISE_AMP = 1.0e-4
SEED = 42

# Per-channel layout: (offset_hz, talk_start_s, audio_tone_hz)
_CARRIERS: list[tuple[float, float, float]] = [
    (-400_000.0, 1.0,  400.0),
    (-300_000.0, 2.0,  600.0),
    (-200_000.0, 3.0,  800.0),
    (-100_000.0, 4.0, 1000.0),
    (+100_000.0, 5.0, 1200.0),
    (+200_000.0, 6.0, 1400.0),
    (+300_000.0, 7.0, 1600.0),
    (+400_000.0, 8.0, 1800.0),
]
TALK_LEN_S = 1.0   # each channel talks for 1 s
AUDIO_AMP = 0.7

OUT_PATH = (
    Path(__file__).resolve().parent.parent
    / "tests" / "fixtures" / "recorded_iq" / "nbfm_multichannel.cf32"
)


def _build_carrier(
    offset_hz: float,
    talk_start_s: float,
    audio_tone_hz: float,
    n_total: int,
) -> np.ndarray:
    """Return the complex64 baseband contribution of one channel.

    Zero outside the channel's talk window; FM-modulated tone inside.
    """
    iq = np.zeros(n_total, dtype=np.complex64)
    n_start = int(talk_start_s * SDR_SAMPLE_RATE_HZ)
    n_len = int(TALK_LEN_S * SDR_SAMPLE_RATE_HZ)
    n_end = min(n_start + n_len, n_total)
    if n_start >= n_end:
        return iq

    span = n_end - n_start
    t = np.arange(span) / SDR_SAMPLE_RATE_HZ
    # Sinusoidal audio at audio_tone_hz, amplitude AUDIO_AMP.
    audio = AUDIO_AMP * np.sin(2.0 * np.pi * audio_tone_hz * t)
    # FM-modulate around offset_hz: phase = ∫ 2π · (offset + Kf·audio) dt.
    instant_freq = offset_hz + MAX_DEVIATION_HZ * audio
    phase = 2.0 * np.pi * np.cumsum(instant_freq) / SDR_SAMPLE_RATE_HZ
    iq[n_start:n_end] = (CARRIER_AMP * np.exp(1j * phase)).astype(np.complex64)
    return iq


def synthesize() -> np.ndarray:
    rng = np.random.default_rng(SEED)
    n_total = int(DURATION_S * SDR_SAMPLE_RATE_HZ)

    iq = np.zeros(n_total, dtype=np.complex64)
    for offset_hz, talk_start_s, audio_tone_hz in _CARRIERS:
        iq += _build_carrier(offset_hz, talk_start_s, audio_tone_hz, n_total)

    # Receiver-noise floor across the entire duration.
    noise_re = rng.standard_normal(n_total).astype(np.float32) * NOISE_AMP
    noise_im = rng.standard_normal(n_total).astype(np.float32) * NOISE_AMP
    iq = iq + (noise_re + 1j * noise_im).astype(np.complex64)

    return iq


def main() -> int:
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)

    iq = synthesize()
    iq.tofile(OUT_PATH)

    size_mb = OUT_PATH.stat().st_size / (1024 * 1024)
    cwd = Path.cwd()
    shown = OUT_PATH.relative_to(cwd) if OUT_PATH.is_relative_to(cwd) else OUT_PATH
    print(
        f"Wrote {shown}\n"
        f"  duration   : {DURATION_S} s\n"
        f"  sample rate: {int(SDR_SAMPLE_RATE_HZ):,} Hz\n"
        f"  IQ samples : {len(iq):,}\n"
        f"  file size  : {size_mb:.1f} MiB\n"
        f"  carriers   : {len(_CARRIERS)} NBFM @ -400…+400 kHz "
        f"(100 kHz spacing)\n"
        f"  modulation : NBFM, ±{int(MAX_DEVIATION_HZ)} Hz dev, "
        f"staggered 1 s talk windows\n"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
