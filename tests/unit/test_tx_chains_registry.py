"""Unit tests for the TX chain registry — runs on the host without GR."""

from __future__ import annotations

import sys

import pytest

import gr_dis.engine.tx_chains as tx_chains_pkg
from gr_dis.bridge.pdu.enums import MOD_DETAIL_FM_ANGLE, MOD_MAJOR_ANGLE
from gr_dis.engine.tx_chains import TxModulationChain, get_tx_chain, register_tx


def _reset() -> None:
    tx_chains_pkg._TX_REGISTRY.clear()  # type: ignore[attr-defined]
    tx_chains_pkg._builtins_loaded = False  # type: ignore[attr-defined]
    prefix = "gr_dis.engine.tx_chains."
    for key in [k for k in sys.modules if k.startswith(prefix) and k != f"{prefix}base"]:
        sys.modules.pop(key, None)


@pytest.fixture(autouse=True)
def _isolate() -> object:
    _reset()
    yield
    _reset()


def test_register_tx_adds_to_registry() -> None:
    @register_tx("toy_tx")
    class _Toy(TxModulationChain):
        dis_mod_major = 3
        dis_mod_detail = 1

        def build_tx(self, *a: object, **kw: object) -> object:  # type: ignore[override]
            return object()

    assert tx_chains_pkg._TX_REGISTRY["toy_tx"] is _Toy  # type: ignore[attr-defined]


def test_get_tx_chain_unknown_raises() -> None:
    tx_chains_pkg._builtins_loaded = True  # type: ignore[attr-defined]
    with pytest.raises(KeyError, match="unknown tx chain"):
        get_tx_chain("no_such")


def test_nbfm_tx_is_registered() -> None:
    cls = get_tx_chain("nbfm")
    assert cls.name == "nbfm"
    assert issubclass(cls, TxModulationChain)
    assert cls.dis_mod_major == MOD_MAJOR_ANGLE
    assert cls.dis_mod_detail == MOD_DETAIL_FM_ANGLE


def test_tx_chains_importable_without_gnuradio() -> None:
    sys.modules.pop("gr_dis.engine.tx_chains", None)
    sys.modules.pop("gr_dis.engine.tx_chains.base", None)
    import importlib
    mod = importlib.import_module("gr_dis.engine.tx_chains")
    assert hasattr(mod, "get_tx_chain")
    assert hasattr(mod, "register_tx")
