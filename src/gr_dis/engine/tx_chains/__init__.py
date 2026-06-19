"""TX modulation chain registry.

Mirrors engine/rx_chains/__init__.py.  Adding a new TX chain requires only a
new file + @register_tx decorator — no edits to this file.
"""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING

from gr_dis.engine.tx_chains.base import TxModulationChain

if TYPE_CHECKING:  # pragma: no cover
    from collections.abc import Callable

_TX_REGISTRY: dict[str, type[TxModulationChain]] = {}
_builtins_loaded = False


def register_tx(name: str) -> Callable[[type[TxModulationChain]], type[TxModulationChain]]:
    """Class decorator that registers a TxModulationChain subclass."""

    def _decorator(cls: type[TxModulationChain]) -> type[TxModulationChain]:
        existing = _TX_REGISTRY.get(name)
        if existing is not None and existing is not cls:
            raise ValueError(f"tx chain name {name!r} already registered to {existing!r}")
        cls.name = name
        _TX_REGISTRY[name] = cls
        return cls

    return _decorator


def _load_builtins() -> None:
    global _builtins_loaded
    if _builtins_loaded:
        return
    _builtins_loaded = True
    from pathlib import Path  # noqa: PLC0415

    pkg_dir = Path(__file__).parent
    for path in sorted(pkg_dir.glob("*.py")):
        stem = path.stem
        if stem.startswith("_") or stem == "base":
            continue
        importlib.import_module(f"{__name__}.{stem}")


def get_tx_chain(name: str) -> type[TxModulationChain]:
    """Return the TX chain class for ``name``."""
    _load_builtins()
    try:
        return _TX_REGISTRY[name]
    except KeyError as exc:
        available = sorted(_TX_REGISTRY)
        raise KeyError(
            f"unknown tx chain {name!r}; registered: {available}"
        ) from exc


__all__ = ["TxModulationChain", "get_tx_chain", "register_tx"]
