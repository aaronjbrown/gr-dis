"""Unit tests for the modulation-chain registry.

These tests run on the host without GR installed. They verify the registry's
behaviour (registration, lookup, error paths) and that built-in chain names
exist after module import — without actually constructing any flowgraph.
"""

from __future__ import annotations

import importlib
import sys

import pytest

import gr_dis.engine.rx_chains as chains_pkg
from gr_dis.engine.rx_chains import ModulationChain, register


def _fresh_registry() -> None:
    """Reset the registry between tests that mutate it."""
    chains_pkg._REGISTRY.clear()  # type: ignore[attr-defined]
    chains_pkg._builtins_loaded = False  # type: ignore[attr-defined]
    # Pop all chain sub-modules so @register fires again on the next lookup.
    prefix = "gr_dis.engine.rx_chains."
    for key in [k for k in sys.modules if k.startswith(prefix) and k != f"{prefix}base"]:
        sys.modules.pop(key, None)


@pytest.fixture(autouse=True)
def _isolate_registry() -> None:
    _fresh_registry()
    yield
    _fresh_registry()


def test_register_adds_class_to_registry() -> None:
    @register("toy")
    class _Toy(ModulationChain):
        def build(self, *_args: object, **_kwargs: object) -> object:  # type: ignore[override]
            return object()

    assert chains_pkg._REGISTRY["toy"] is _Toy  # type: ignore[attr-defined]
    assert _Toy.name == "toy"


def test_register_duplicate_same_class_is_idempotent() -> None:
    @register("dup")
    class _A(ModulationChain):
        def build(self, *_args: object, **_kwargs: object) -> object:  # type: ignore[override]
            return object()

    # Re-registering the same class under the same name should be a no-op.
    register("dup")(_A)
    assert chains_pkg._REGISTRY["dup"] is _A  # type: ignore[attr-defined]


def test_register_duplicate_different_class_raises() -> None:
    @register("clash")
    class _A(ModulationChain):
        def build(self, *_args: object, **_kwargs: object) -> object:  # type: ignore[override]
            return object()

    with pytest.raises(ValueError, match="already registered"):
        @register("clash")
        class _B(ModulationChain):
            def build(self, *_args: object, **_kwargs: object) -> object:  # type: ignore[override]
                return object()


def test_get_chain_unknown_raises_keyerror_with_available_list() -> None:
    @register("known")
    class _K(ModulationChain):
        def build(self, *_args: object, **_kwargs: object) -> object:  # type: ignore[override]
            return object()

    # Force builtins-loaded so we don't trip on the lazy nbfm import here.
    chains_pkg._builtins_loaded = True  # type: ignore[attr-defined]

    with pytest.raises(KeyError, match="unknown chain 'no_such'"):
        chains_pkg.get_chain("no_such")


def test_nbfm_chain_is_registered_after_lookup() -> None:
    """get_chain('nbfm') must succeed even on a GR-less host.

    The class body of NBFMChain does not touch gnuradio (only its ``build``
    method does), so importing it during registry warmup is safe.
    """
    cls = chains_pkg.get_chain("nbfm")
    assert cls.name == "nbfm"
    assert issubclass(cls, ModulationChain)


def test_registered_names_includes_nbfm() -> None:
    names = chains_pkg.registered_names()
    assert "nbfm" in names


def test_chains_package_importable_without_gnuradio() -> None:
    """Importing the package itself must not require gnuradio.

    Forcing a reimport of ``gr_dis.engine.rx_chains`` should not pull gnuradio in.
    """
    sys.modules.pop("gr_dis.engine.rx_chains", None)
    sys.modules.pop("gr_dis.engine.rx_chains.base", None)
    mod = importlib.import_module("gr_dis.engine.rx_chains")
    assert hasattr(mod, "register")
    assert hasattr(mod, "get_chain")
