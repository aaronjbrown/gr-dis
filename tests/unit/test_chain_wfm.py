"""Unit tests for the WFM modulation chain.

Runs on the host without GNU Radio installed. Verifies registration, class
hierarchy, and that the module is importable on a GR-less host.
"""

from __future__ import annotations

import sys

from gr_dis.engine.rx_chains import ModulationChain, get_chain, registered_names
from gr_dis.engine.rx_chains.wfm import _AUDIO_DECIMATION, _AUDIO_RATE_HZ, _QUAD_RATE_HZ, WFMChain


def test_wfm_chain_is_registered() -> None:
    cls = get_chain("wfm")
    assert cls is WFMChain
    assert cls.name == "wfm"
    assert issubclass(cls, ModulationChain)


def test_registered_names_includes_wfm() -> None:
    assert "wfm" in registered_names()


def test_wfm_and_nbfm_coexist_in_registry() -> None:
    names = registered_names()
    assert "wfm" in names
    assert "nbfm" in names


def test_audio_decimation_consistent() -> None:
    """_QUAD_RATE_HZ / _AUDIO_DECIMATION must equal _AUDIO_RATE_HZ exactly."""
    assert _QUAD_RATE_HZ % _AUDIO_DECIMATION == 0
    assert _QUAD_RATE_HZ // _AUDIO_DECIMATION == _AUDIO_RATE_HZ


def test_wfm_module_importable_without_gnuradio() -> None:
    """wfm.py must not pull gnuradio in at module scope.

    The top-level import of WFMChain in this file already proves this — if
    gnuradio were required at class-definition time, the whole test file would
    fail to collect. This test exists as an explicit, named assertion of that
    contract.
    """
    assert WFMChain is not None
    # gnuradio must not have been imported as a side effect of importing wfm.py
    assert "gnuradio" not in sys.modules
