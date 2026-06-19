"""Wide-band FM (broadcast FM, WBFM) modulation chain.

Pipeline (complex baseband at ``input_sample_rate_hz`` → mono int16 PCM at 8 kHz):

    freq_xlating_fir_filter_ccc        # channel select + decimate to ~quad_rate
        ↓
    rational_resampler_ccc             # trim to exactly quad_rate (240 kHz)
        ↓
    analog.pwr_squelch_cc              # gate low-power frames
        ↓
    analog.wfm_rcv                     # FM demod + 75 µs de-emphasis + mono audio
        ↓
    blocks.multiply_const_ff(32767)    # float → int16-scale
        ↓
    blocks.float_to_short              # cast to int16

``wfm_rcv`` internally calls ``analog.fm_demod_cf`` with deviation=75 kHz,
audio_pass=15 kHz, and tau=75 µs. The passband edge (15 kHz) sits below the
stereo pilot at 19 kHz, so only the mono L+R component survives — the
subcarrier at 38 kHz ± 15 kHz is rejected. This is the correct stereo-collapse
to mono for a voice or DIS Signal PDU consumer.

The expected ``bandwidth_hz`` in the channel config is 200000 (200 kHz) to
cover the full ±75 kHz FM deviation. The SDR ``sample_rate_hz`` must therefore
be at least ~400 kHz to fit one WFM channel within the Nyquist window.

All ``gnuradio`` imports are deferred to :meth:`WFMChain.build` so this module
imports cleanly on the host (no GR install required for the unit-test suite).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from gr_dis.engine.rx_chains import register
from gr_dis.engine.rx_chains.base import ModulationChain

if TYPE_CHECKING:  # pragma: no cover
    from gnuradio import gr


_QUAD_RATE_HZ = 240_000   # FM demod intermediate rate — wide enough for 75 kHz deviation
_AUDIO_RATE_HZ = 8_000    # output PCM sample rate (DIS Signal payload)
_AUDIO_DECIMATION = _QUAD_RATE_HZ // _AUDIO_RATE_HZ  # 30 → 240000/30 = 8000 Hz


@register("wfm")
class WFMChain(ModulationChain):
    """Wide-band FM broadcast receiver chain (e.g. FM broadcast 88–108 MHz)."""

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

        squelch_db = float(chain_config.get("squelch_db", -60.0))
        squelch_ramp_ms = float(chain_config.get("squelch_ramp_ms", 50.0))

        # --- Stage 1: channel select + coarse decimation -------------------
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
        from math import gcd  # noqa: PLC0415

        target_num = int(round(_QUAD_RATE_HZ))
        target_den = int(round(coarse_rate))
        g = gcd(target_num, target_den) or 1
        resampler = filter.rational_resampler_ccc(
            interpolation=target_num // g,
            decimation=target_den // g,
        )

        # --- Stage 3: squelch ----------------------------------------------
        ramp_samples = max(1, int(squelch_ramp_ms * _QUAD_RATE_HZ / 1000.0))
        squelch = analog.pwr_squelch_cc(
            squelch_db,
            1e-3,
            ramp_samples,
            False,  # gate=False keeps streaming zeros while muted
        )

        # --- Stage 4: WFM demod → mono audio at 8 kHz ----------------------
        # wfm_rcv uses deviation=75 kHz, audio_pass=15 kHz, tau=75 µs
        # internally. Output rate = _QUAD_RATE_HZ / _AUDIO_DECIMATION = 8 kHz.
        demod = analog.wfm_rcv(
            quad_rate=_QUAD_RATE_HZ,
            audio_decimation=_AUDIO_DECIMATION,
        )

        # --- Stage 5: float → int16 ----------------------------------------
        scaler = blocks.multiply_const_ff(32767.0)
        to_short = blocks.float_to_short(1, 1.0)

        # --- Hier block I/O ------------------------------------------------
        hb = gr.hier_block2(
            "WFMChain",
            gr.io_signature(1, 1, gr.sizeof_gr_complex),
            gr.io_signature(1, 1, gr.sizeof_short),
        )

        hb.connect(hb, xlating)
        hb.connect(xlating, resampler)
        hb.connect(resampler, squelch)
        hb.connect(squelch, demod)
        hb.connect(demod, scaler)
        hb.connect(scaler, to_short)
        hb.connect(to_short, hb)

        hb._squelch = squelch
        return hb
