"""Abstract base class for Modulation Chains.

A ModulationChain takes a complex baseband stream at the SDR sample rate and
produces a mono int16 audio stream at 8 kHz. Subclasses live in this package
and self-register via :func:`gr_dis.engine.rx_chains.register`.

The gnuradio import is deferred to :meth:`ModulationChain.build` so the ABC
itself is importable on the host (where gnuradio is not installed) — which
keeps the unit-test suite green outside the toolbox container.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - import guard
    from gnuradio import gr


class ModulationChain(ABC):
    """Interface every chain subclass must implement.

    Attributes:
        name: Registry key (set by ``@register("name")``).
    """

    name: str = ""

    @abstractmethod
    def build(
        self,
        input_sample_rate_hz: float,
        channel_bandwidth_hz: float,
        rf_offset_hz: float,
        chain_config: dict[str, Any],
    ) -> gr.hier_block2:
        """Construct the hier block for this chain.

        Args:
            input_sample_rate_hz: SDR-side complex baseband sample rate.
            channel_bandwidth_hz: Channel bandwidth, used to size filters.
            rf_offset_hz: Difference between channel RF and SDR center freq.
                The chain is responsible for frequency-translating to baseband.
            chain_config: ``chain_config`` dict from the channel's YAML config.

        Returns:
            A ``gnuradio.gr.hier_block2`` with one complex input port
            (baseband IQ at ``input_sample_rate_hz``) and one int16 output
            port (mono PCM at 8 kHz).
        """
        ...
