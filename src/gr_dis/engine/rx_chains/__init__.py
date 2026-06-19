"""Modulation chain registry.

Chain subclasses self-register at import time via ``@register("name")``.
Built-in chains live in this package and are lazily imported the first time
:func:`get_chain` is called — so importing ``gr_dis.engine.rx_chains`` itself
does not require gnuradio.
"""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING

from gr_dis.engine.rx_chains.base import ModulationChain

if TYPE_CHECKING:  # pragma: no cover
    from collections.abc import Callable

# Map of registered chain name → ModulationChain subclass.
_REGISTRY: dict[str, type[ModulationChain]] = {}

_builtins_loaded = False


def register(name: str) -> Callable[[type[ModulationChain]], type[ModulationChain]]:
    """Class decorator that registers a ModulationChain subclass.

    Raises:
        ValueError: if ``name`` is already registered to a different class.
    """

    def _decorator(cls: type[ModulationChain]) -> type[ModulationChain]:
        existing = _REGISTRY.get(name)
        if existing is not None and existing is not cls:
            raise ValueError(
                f"chain name {name!r} already registered to {existing!r}"
            )
        cls.name = name
        _REGISTRY[name] = cls
        return cls

    return _decorator


def _load_builtins() -> None:
    """Import all chain modules in this package, registering them as a side effect.

    Any .py file in the rx_chains/ directory (excluding __init__ and base) is
    treated as a chain module. Adding a new chain therefore requires only a
    new file + @register decorator — no edits to this file.
    """
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


def get_chain(name: str) -> type[ModulationChain]:
    """Return the chain class for ``name``.

    Triggers a one-time import of built-in chain modules.

    Raises:
        KeyError: if ``name`` is not registered.
    """
    _load_builtins()
    try:
        return _REGISTRY[name]
    except KeyError as exc:
        available = sorted(_REGISTRY)
        raise KeyError(
            f"unknown chain {name!r}; registered chains: {available}"
        ) from exc


def registered_names() -> list[str]:
    """Return sorted list of registered chain names (after loading built-ins)."""
    _load_builtins()
    return sorted(_REGISTRY)


__all__ = ["ModulationChain", "get_chain", "register", "registered_names"]
