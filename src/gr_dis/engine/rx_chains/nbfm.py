"""Narrow-band FM modulation chain.

Pipeline (complex baseband at ``input_sample_rate_hz`` → mono int16 PCM at 8 kHz):

    freq_xlating_fir_filter_ccc        # channel select + decimate to ~quad_rate
        ↓
    rational_resampler_ccc             # trim to exactly quad_rate (48 kHz)
        ↓
    analog.pwr_squelch_cc              # mute on low power
        ↓
    analog.nbfm_rx                     # FM demod + audio LPF + decimate to 8 kHz
        ↓
    blocks.multiply_const_ff(32767)    # float → int16-scale
        ↓
    blocks.float_to_short              # cast to int16

All ``gnuradio`` imports are deferred to :meth:`NBFMChain.build` so this module
imports cleanly on the host (no GR install required for the unit-test suite).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from gr_dis.engine.rx_chains import register
from gr_dis.engine.rx_chains.base import ModulationChain

if TYPE_CHECKING:  # pragma: no cover
    from gnuradio import gr


_QUAD_RATE_HZ = 48_000   # FM demod sample rate (nbfm_rx quad rate)
_AUDIO_RATE_HZ = 8_000   # output PCM sample rate (DIS Signal payload)


@register("nbfm")
class NBFMChain(ModulationChain):
    """Narrow-band FM voice receiver chain (e.g. amateur 2 m, marine VHF)."""

    def build(
        self,
        input_sample_rate_hz: float,
        channel_bandwidth_hz: float,
        rf_offset_hz: float,
        chain_config: dict[str, Any],
    ) -> gr.hier_block2:
        # Deferred GR imports — see module docstring.
        from gnuradio import analog, blocks, filter, gr  # noqa: PLC0415
        from gnuradio.filter import firdes  # noqa: PLC0415

        # --- Effective chain parameters (with defaults from NBFMChainConfig) ---
        max_deviation_hz = float(chain_config.get("deviation_hz", 5_000.0))
        audio_lpf_hz = float(chain_config.get("audio_lpf_hz", 3_400.0))
        squelch_db = float(chain_config.get("squelch_db", -60.0))
        squelch_ramp_ms = float(chain_config.get("squelch_ramp_ms", 50.0))

        # --- Stage 1: channel select + coarse decimation -------------------
        # Decimate roughly to quad_rate. If input_sample_rate isn't an integer
        # multiple of quad_rate, the rational_resampler below cleans up the
        # remainder.
        coarse_decim = max(1, int(round(input_sample_rate_hz / _QUAD_RATE_HZ)))
        coarse_rate = input_sample_rate_hz / coarse_decim

        channel_taps = firdes.low_pass(
            1.0,
            input_sample_rate_hz,
            channel_bandwidth_hz / 2.0,
            channel_bandwidth_hz * 0.2,
        )
        xlating = filter.freq_xlating_fir_filter_ccc(
            coarse_decim,
            channel_taps,
            rf_offset_hz,
            input_sample_rate_hz,
        )

        # --- Stage 2: exact rate to QUAD_RATE_HZ ---------------------------
        # ``rational_resampler_ccc`` takes integer interp/decim; reduce the
        # ratio coarse_rate : QUAD_RATE_HZ to lowest terms.
        from math import gcd  # noqa: PLC0415

        target_num = int(round(_QUAD_RATE_HZ))
        target_den = int(round(coarse_rate))
        g = gcd(target_num, target_den) or 1
        resampler = filter.rational_resampler_ccc(
            interpolation=target_num // g,
            decimation=target_den // g,
        )

        # --- Stage 3: squelch ----------------------------------------------
        # pwr_squelch_cc(threshold_db, alpha, ramp, gate)
        # ramp is in samples; convert from ms using QUAD_RATE_HZ.
        # alpha is the IIR coefficient for the power-detector smoother.
        # GR's default (1e-4) gives τ ≈ 200 ms at 48 kHz — far too slow for
        # voice radios (carrier drops on PTT release would leave the squelch
        # detector "remembering" a strong signal for several seconds). 1e-3
        # gives τ ≈ 21 ms / 5τ ≈ 100 ms; tracks voice-PTT timing.
        ramp_samples = max(1, int(squelch_ramp_ms * _QUAD_RATE_HZ / 1000.0))
        squelch = analog.pwr_squelch_cc(
            squelch_db,
            1e-3,
            ramp_samples,
            False,  # gate=False keeps streaming zeros while muted
        )

        # --- Stage 4: NBFM demod (also resamples quad_rate → audio_rate) ---
        demod = analog.nbfm_rx(
            audio_rate=_AUDIO_RATE_HZ,
            quad_rate=_QUAD_RATE_HZ,
            tau=75e-6,
            max_dev=max_deviation_hz,
        )

        # --- Stage 5: audio LPF (extra band-limit before int16 cast) -------
        audio_taps = firdes.low_pass(
            1.0, _AUDIO_RATE_HZ, audio_lpf_hz, max(200.0, audio_lpf_hz * 0.15)
        )
        audio_lpf = filter.fir_filter_fff(1, audio_taps)

        # --- Stage 6: float → int16 ---------------------------------------
        scaler = blocks.multiply_const_ff(32767.0)
        to_short = blocks.float_to_short(1, 1.0)

        # --- Hier block I/O -----------------------------------------------
        hb = gr.hier_block2(
            "NBFMChain",
            gr.io_signature(1, 1, gr.sizeof_gr_complex),
            gr.io_signature(1, 1, gr.sizeof_short),
        )

        # Connect: self(in) → xlating → resampler → squelch → demod
        #        → audio_lpf → scaler → to_short → self(out)
        hb.connect(hb, xlating)
        hb.connect(xlating, resampler)
        hb.connect(resampler, squelch)
        hb.connect(squelch, demod)
        hb.connect(demod, audio_lpf)
        hb.connect(audio_lpf, scaler)
        hb.connect(scaler, to_short)
        hb.connect(to_short, hb)

        # Keep references so the squelch object is reachable by ZMQ sink if it
        # wants to inspect ``unmuted()``. We expose this for free since GR
        # already pins these via the hier block graph.
        hb._squelch = squelch
        return hb
