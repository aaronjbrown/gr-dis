"""Unit tests: /healthz 200 vs 503 based on channel health."""

from __future__ import annotations

import asyncio
import threading
import urllib.error
import urllib.request

import pytest
from prometheus_client import CollectorRegistry

from gr_dis.bridge.main import _guarded_heartbeat
from gr_dis.metrics import BridgeMetrics, start_metrics_server

_PORT_ALIVE = 55192
_PORT_DEAD = 55193


def test_healthz_200_when_all_channels_alive() -> None:
    metrics = BridgeMetrics(registry=CollectorRegistry())
    server = start_metrics_server(metrics, f"127.0.0.1:{_PORT_ALIVE}")
    try:
        with urllib.request.urlopen(
            f"http://127.0.0.1:{_PORT_ALIVE}/healthz", timeout=2
        ) as resp:
            assert resp.status == 200
            assert b"OK" in resp.read()
    finally:
        server.shutdown()


def test_healthz_503_when_channel_dead() -> None:
    metrics = BridgeMetrics(registry=CollectorRegistry())
    metrics.mark_channel_dead("ch_broken")
    server = start_metrics_server(metrics, f"127.0.0.1:{_PORT_DEAD}")
    try:
        with pytest.raises(urllib.error.HTTPError) as exc_info:
            urllib.request.urlopen(
                f"http://127.0.0.1:{_PORT_DEAD}/healthz", timeout=2
            )
        assert exc_info.value.code == 503
    finally:
        server.shutdown()


class _AlwaysFailingHandler:
    """Stand-in for RadioChannelHandler whose heartbeat_loop always crashes."""

    channel_id = "ch_fail"

    async def heartbeat_loop(self) -> None:
        raise RuntimeError("synthetic heartbeat crash")


async def test_guarded_heartbeat_marks_dead_after_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Collapse the linear back-off so the test runs instantly.
    real_sleep = asyncio.sleep
    monkeypatch.setattr(
        "gr_dis.bridge.main.asyncio.sleep",
        lambda _delay: real_sleep(0),
    )

    metrics = BridgeMetrics(registry=CollectorRegistry())
    assert metrics.all_channels_alive()

    await _guarded_heartbeat(_AlwaysFailingHandler(), metrics)  # type: ignore[arg-type]

    assert not metrics.all_channels_alive()
    assert "ch_fail" in metrics._dead_channels


def test_dead_channels_lock_concurrent() -> None:
    """mark_channel_dead and all_channels_alive must be safe under concurrent access."""
    metrics = BridgeMetrics(registry=CollectorRegistry())
    barrier = threading.Barrier(2)
    errors: list[Exception] = []

    def writer() -> None:
        try:
            barrier.wait()
            for i in range(200):
                metrics.mark_channel_dead(f"ch_{i}")
        except Exception as exc:  # pragma: no cover
            errors.append(exc)

    def reader() -> None:
        try:
            barrier.wait()
            for _ in range(200):
                metrics.all_channels_alive()
        except Exception as exc:  # pragma: no cover
            errors.append(exc)

    t1 = threading.Thread(target=writer)
    t2 = threading.Thread(target=reader)
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    assert not errors
    # After 200 writes the set must be non-empty.
    assert not metrics.all_channels_alive()
