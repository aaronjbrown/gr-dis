#!/usr/bin/env python3
"""Synthesize a WFM broadcast IQ fixture.

Generates ``tests/fixtures/recorded_iq/wfm_broadcast.cf32`` — a complex-float-32
baseband recording that, when fed to ``gr-dis run --source-file ...`` with
``examples/config_wfm.yaml``, exercises the ``fm_ch1`` channel
(rf_freq 100.1 MHz, offset +100 kHz from the SDR center of 100.0 MHz):

  * 0.0 – 0.4 s : no carrier → channel squelch closed, no Signal PDUs
  * 0.4 – 2.4 s : carrier + FM-modulated 500→3000 Hz chirp at ±75 kHz dev →
                   squelch open, Signal PDUs carrying an audible audio sweep
  * 2.4 – 4.0 s : no carrier → squelch closes again (allow ≥0.5 s for the
                   pwr_squelch detector smoother to relax below threshold)

When played back with ``repeat=True`` (the default in ``engine.capture``),
this cycle repeats indefinitely, giving the Bridge clean
``squelch_open``/``squelch_close`` transitions every 4 seconds.

The fixture is mono-only — no 19 kHz stereo pilot or 38 kHz L−R subcarrier.
The WFM chain uses ``analog.wfm_rcv`` which low-passes the demodulated
composite at 15 kHz, so stereo subcarriers would be discarded anyway; their
absence simplifies the fixture without affecting what the chain receives.

No audio pre-emphasis is applied. The ``wfm_rcv`` block applies 75 µs
de-emphasis on receive (3 dB corner ≈ 2.1 kHz), which gives a mild high-end
rolloff in the 500–3000 Hz test chirp — audible but not distorting. This
matches the NBFM fixture's design (also unemphasised) and keeps the two
scripts symmetrical.

Re-run this script any time ``examples/config_wfm.yaml``'s center/sample-rate
or the target channel offset changes.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

# --- Parameters keyed off examples/config_wfm.yaml ---------------------------
SDR_CENTER_HZ = 100_000_000.0
SDR_SAMPLE_RATE_HZ = 2_400_000.0
TARGET_CHANNEL_HZ = 100_100_000.0     # examples/config_wfm.yaml :: fm_ch1
RF_OFFSET_HZ = TARGET_CHANNEL_HZ - SDR_CENTER_HZ   # +100_000

AUDIO_RATE_HZ = 8_000
AUDIO_AMPLITUDE = 0.7                  # FM modulator input level (≤1.0)
MAX_DEVIATION_HZ = 75_000.0            # WFM broadcast standard (matches wfm_rcv internal)

DURATION_S = 4.0
SILENCE_BEFORE_S = 0.4
TALK_S = 2.0
# Remainder (≈1.6 s) is post-talk silence. Comfortably longer than the
# WFM chain's squelch close time (τ ≈ 21 ms at alpha=1e-3 in pwr_squelch_cc,
# so the detector lands at noise floor within ~0.5 s of carrier drop).

CARRIER_AMP = 0.9                      # IQ unit-circle amplitude during talk
# Per-component AWGN std-dev. Total |noise|^2 ≈ 2·NOISE_AMP^2.
# 1e-4 → noise power ≈ -77 dB (below default squelch_db = -60 dB so the
# default WFM squelch can mute the silence windows cleanly).
NOISE_AMP = 1.0e-4
SEED = 42                              # reproducible noise

# Audio chirp range — wider than NBFM's 400→1200 Hz to take advantage of WFM's
# higher audio fidelity. Stays under 4 kHz (8 kHz output Nyquist).
AUDIO_F0_HZ = 500.0
AUDIO_F1_HZ = 3_000.0

OUT_PATH = (
    Path(__file__).resolve().parent.parent
    / "tests" / "fixtures" / "recorded_iq" / "wfm_broadcast.cf32"
)


def _build_audio_baseband() -> np.ndarray:
    """Build a smooth audio waveform at AUDIO_RATE_HZ for the talk window."""
    talk_samples = int(TALK_S * AUDIO_RATE_HZ)
    t = np.arange(talk_samples) / AUDIO_RATE_HZ
    # Linear chirp AUDIO_F0_HZ → AUDIO_F1_HZ; phase = ∫ inst_freq dt.
    inst_freq = AUDIO_F0_HZ + (AUDIO_F1_HZ - AUDIO_F0_HZ) * (t / TALK_S)
    phase = 2.0 * np.pi * np.cumsum(inst_freq) / AUDIO_RATE_HZ
    return (AUDIO_AMPLITUDE * np.sin(phase)).astype(np.float32)


def _upsample_linear(x: np.ndarray, factor: int) -> np.ndarray:
    """Linear-interpolate ``x`` by ``factor``. Smoother than ``np.repeat``."""
    n_in = len(x)
    n_out = n_in * factor
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
    if len(audio_up) < n_talk:
        audio_up = np.concatenate(
            [audio_up, np.zeros(n_talk - len(audio_up), dtype=np.float32)]
        )
    audio_up = audio_up[:n_talk]

    # --- FM modulate the talk window: phase = ∫ 2π · (offset + Kf·audio) dt -
    instant_freq = RF_OFFSET_HZ + MAX_DEVIATION_HZ * audio_up.astype(np.float64)
    phase_talk = 2.0 * np.pi * np.cumsum(instant_freq) / SDR_SAMPLE_RATE_HZ
    iq_talk = (CARRIER_AMP * np.exp(1j * phase_talk)).astype(np.complex64)

    # --- Splice silence | talk | silence -------------------------------------
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
        f"  modulation : WFM, ±{int(MAX_DEVIATION_HZ):,} Hz dev, "
        f"{int(AUDIO_F0_HZ)}→{int(AUDIO_F1_HZ)} Hz chirp (mono)\n"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
