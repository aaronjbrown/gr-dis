"""NBFM TX modulation chain.

Pipeline (int16 PCM at 8 kHz → complex baseband at output_sample_rate_hz):

    blocks.short_to_float
        → blocks.multiply_const_ff(1/32767.0)
        → analog.nbfm_tx(audio_rate=8000, quad_rate=48000, tau=75e-6, max_dev=deviation_hz)
        → rational_resampler_ccc (48000 → output_sample_rate_hz)
        → freq_xlating_fir_filter_ccc (shift to rf_offset_hz)

All gnuradio imports are deferred to build_tx().
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from gr_dis.bridge.pdu.enums import MOD_DETAIL_FM_ANGLE, MOD_MAJOR_ANGLE
from gr_dis.engine.tx_chains import register_tx
from gr_dis.engine.tx_chains.base import TxModulationChain

if TYPE_CHECKING:  # pragma: no cover
    from gnuradio import gr

_QUAD_RATE_HZ = 48_000
_AUDIO_RATE_HZ = 8_000


@register_tx("nbfm")
class NBFMTxChain(TxModulationChain):
    """Narrow-band FM TX chain."""

    dis_mod_major = MOD_MAJOR_ANGLE   # 3
    dis_mod_detail = MOD_DETAIL_FM_ANGLE  # 1

    def build_tx(
        self,
        output_sample_rate_hz: float,
        rf_offset_hz: float,
        channel_bandwidth_hz: float,
        chain_config: dict[str, Any],
    ) -> gr.hier_block2:
        from gnuradio import analog, blocks, filter, gr  # noqa: PLC0415
        from gnuradio.filter import firdes  # noqa: PLC0415

        deviation_hz = float(chain_config.get("deviation_hz", 5_000.0))

        hb = gr.hier_block2(
            "NBFMTxChain",
            gr.io_signature(1, 1, gr.sizeof_short),
            gr.io_signature(1, 1, gr.sizeof_gr_complex),
        )

        s2f = blocks.short_to_float(1, 1.0)
        scale = blocks.multiply_const_ff(1.0 / 32767.0)
        nbfm = analog.nbfm_tx(
            audio_rate=_AUDIO_RATE_HZ,
            quad_rate=_QUAD_RATE_HZ,
            tau=75e-6,
            max_dev=deviation_hz,
        )

        interp = int(output_sample_rate_hz) // _QUAD_RATE_HZ
        decim = 1
        resamp = filter.rational_resampler_ccc(
            interpolation=interp,
            decimation=decim,
        )

        taps = firdes.low_pass(
            1.0, output_sample_rate_hz, channel_bandwidth_hz / 2, channel_bandwidth_hz / 10
        )
        xlate = filter.freq_xlating_fir_filter_ccc(1, taps, rf_offset_hz, output_sample_rate_hz)

        hb.connect(hb, s2f, scale, nbfm, resamp, xlate, hb)
        return hb
