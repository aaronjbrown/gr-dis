"""Verify zmq_source.py imports cleanly on the host (no GR required)."""

from __future__ import annotations


def test_zmq_source_importable_without_gnuradio() -> None:
    import importlib
    mod = importlib.import_module("gr_dis.engine.zmq_source")
    assert hasattr(mod, "make_zmq_tx_source")
