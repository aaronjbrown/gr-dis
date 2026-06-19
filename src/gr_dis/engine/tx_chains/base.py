"""Abstract base class for TX modulation chains.

Input: int16 PCM at 8 kHz.  Output: complex baseband at SDR sample rate.
GR imports are deferred to build_tx() so this module is safe to import on the host.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover
    from gnuradio import gr


class TxModulationChain(ABC):
    """Interface for TX chain subclasses.

    Class attributes ``dis_mod_major`` and ``dis_mod_detail`` must be set
    and must match the corresponding entries in ``bridge.pdu.enums.CHAIN_MODULATION``.
    """

    name: str = ""
    dis_mod_major: int = 0
    dis_mod_detail: int = 0

    @abstractmethod
    def build_tx(
        self,
        output_sample_rate_hz: float,
        rf_offset_hz: float,
        channel_bandwidth_hz: float,
        chain_config: dict[str, Any],
    ) -> gr.hier_block2:
        """Construct the TX hier block.

        Args:
            output_sample_rate_hz: SDR-side complex baseband sample rate.
            rf_offset_hz: Channel RF − SDR centre freq.  The chain must
                frequency-translate to this offset before the SoapySDR sink.
            channel_bandwidth_hz: Channel bandwidth (used to size filters).
            chain_config: ``chain_config`` dict from the channel's YAML config.

        Returns:
            A ``gr.hier_block2`` with one int16 input port (PCM at 8 kHz)
            and one complex output port (baseband at ``output_sample_rate_hz``).
        """
        ...
