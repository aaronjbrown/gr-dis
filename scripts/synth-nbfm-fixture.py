#!/usr/bin/env python3
"""Synthesize an NBFM voice IQ fixture.

Generates ``tests/fixtures/recorded_iq/nbfm_voice.cf32`` — a complex-float-32
baseband recording that, when fed to ``gr-dis run --source-file ...`` with
``examples/config.example.yaml``, exercises the ``vhf_ch1`` channel
(rf_freq 144.8 MHz, offset −700 kHz from the SDR center of 145.5 MHz):

  * 0.0 – 0.4 s : no carrier  → channel squelch closed, no Signal PDUs
  * 0.4 – 2.4 s : carrier + FM-modulated 400→1200 Hz chirp  → squelch open,
                   Signal PDUs carrying an audible audio sweep
  * 2.4 – 4.0 s : no carrier  → squelch closes again (allow ≥1 s for the
                   pwr_squelch detector smoother to relax below threshold)

When played back with ``repeat=True`` (the default in ``engine.capture``),
this cycle repeats indefinitely, giving the Bridge clean
``squelch_open``/``squelch_close`` transitions every 4 seconds.

The fixture is intentionally synthetic, not over-the-air: it has no off-air
QRM and a clean (noisy) carrier, but it's good enough to demonstrate the
full RF → audio → PDU pipeline without needing an SDR.

Re-run this script any time the example config's center/sample-rate or the
target channel offset changes.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

# --- Parameters keyed off examples/config.example.yaml -----------------------
SDR_CENTER_HZ = 145_500_000.0
SDR_SAMPLE_RATE_HZ = 2_400_000.0
TARGET_CHANNEL_HZ = 144_800_000.0     # examples/config.example.yaml :: vhf_ch1
RF_OFFSET_HZ = TARGET_CHANNEL_HZ - SDR_CENTER_HZ   # -700_000

AUDIO_RATE_HZ = 8_000
AUDIO_AMPLITUDE = 0.7                  # FM modulator input level (≤1.0)
MAX_DEVIATION_HZ = 5_000.0             # matches NBFMChainConfig default

DURATION_S = 4.0
SILENCE_BEFORE_S = 0.4
TALK_S = 2.0
# Remainder (≈1.6 s) is post-talk silence. Comfortably longer than the
# NBFM chain's squelch close time (τ ≈ 21 ms at alpha=1e-3, so the
# detector lands at noise floor well within 0.5 s of carrier drop).

CARRIER_AMP = 0.9                      # IQ unit-circle amplitude during talk
# Per-component AWGN std-dev. Total |noise|^2 ≈ 2·NOISE_AMP^2.
# 1e-4 → noise power ≈ -77 dB (below default squelch_db = -60 dB so the
# default NBFM squelch can mute the silence windows cleanly).
NOISE_AMP = 1.0e-4
SEED = 42                              # reproducible noise

OUT_PATH = (
    Path(__file__).resolve().parent.parent
    / "tests" / "fixtures" / "recorded_iq" / "nbfm_voice.cf32"
)


def _build_audio_baseband() -> np.ndarray:
    """Build a smooth audio waveform at AUDIO_RATE_HZ for the talk window."""
    talk_samples = int(TALK_S * AUDIO_RATE_HZ)
    t = np.arange(talk_samples) / AUDIO_RATE_HZ
    # Linear chirp 400 → 1200 Hz; phase is integral of instantaneous freq.
    f0, f1 = 400.0, 1200.0
    inst_freq = f0 + (f1 - f0) * (t / TALK_S)
    phase = 2.0 * np.pi * np.cumsum(inst_freq) / AUDIO_RATE_HZ
    return (AUDIO_AMPLITUDE * np.sin(phase)).astype(np.float32)


def _upsample_linear(x: np.ndarray, factor: int) -> np.ndarray:
    """Linear-interpolate ``x`` by ``factor``. Smoother than ``np.repeat``."""
    n_in = len(x)
    n_out = n_in * factor
    # Sample positions in the input space
    idx = np.arange(n_out) / factor
    i0 = np.floor(idx).astype(np.int64)
    i1 = np.clip(i0 + 1, 0, n_in - 1)
    frac = (idx - i0).astype(np.float32)
    return ((1.0 - frac) * x[i0] + frac * x[i1]).astype(np.float32)


def synthesize() -> np.ndarray:
    rng = np.random.default_rng(SEED)

    n_total = int(DURATION_S * SDR_SAMPLE_RATE_HZ)
    n_silence_before = int(SILENCE_BEFORE_S * SDR_SAMPLE_RATE_HZ)
    n_talk = int(TALK_S * SDR_SAMPLE_RATE_HZ)

    # --- Build modulating audio at full baseband rate ------------------------
    audio_8k = _build_audio_baseband()
    upsample_factor = int(SDR_SAMPLE_RATE_HZ // AUDIO_RATE_HZ)   # 300
    audio_up = _upsample_linear(audio_8k, upsample_factor)
    # Trim/pad to exactly n_talk just in case rounding bit us by ±a few samples
    if len(audio_up) < n_talk:
        audio_up = np.concatenate(
            [audio_up, np.zeros(n_talk - len(audio_up), dtype=np.float32)]
        )
    audio_up = audio_up[:n_talk]

    # --- FM modulate the talk window: phase = ∫ 2π · (offset + Kf·audio) dt -
    instant_freq = RF_OFFSET_HZ + MAX_DEVIATION_HZ * audio_up.astype(np.float64)
    phase_talk = 2.0 * np.pi * np.cumsum(instant_freq) / SDR_SAMPLE_RATE_HZ
    # Match phase units (cumsum/fs == ∫ dt for uniformly sampled signal)
    iq_talk = (CARRIER_AMP * np.exp(1j * phase_talk)).astype(np.complex64)

    # --- Splice silence | talk | silence ------------------------------------
    iq = np.zeros(n_total, dtype=np.complex64)
    iq[n_silence_before : n_silence_before + n_talk] = iq_talk

    # --- Add AWGN everywhere (mimics receiver noise floor) -------------------
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
        f"  target     : {int(TARGET_CHANNEL_HZ):,} Hz "
        f"(offset {int(RF_OFFSET_HZ):+,} Hz from SDR center)\n"
        f"  modulation : NBFM, ±{int(MAX_DEVIATION_HZ)} Hz dev, "
        f"400→1200 Hz chirp\n"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
