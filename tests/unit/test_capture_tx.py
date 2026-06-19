"""Verify capture.py imports cleanly after TX additions."""

from __future__ import annotations


def test_capture_importable() -> None:
    import importlib
    mod = importlib.import_module("gr_dis.engine.capture")
    assert hasattr(mod, "build_top_block")
    assert hasattr(mod, "run_capture")
