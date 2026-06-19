"""Integration-lite test: stress publisher into a real SUB socket, no bridge."""

from __future__ import annotations

import asyncio

import msgpack
import zmq
import zmq.asyncio

from tests.integration._stress_publisher import (
    ChannelSpec,
    MultiChannelStressPublisher,
)

_Frame = tuple[bytes, dict[str, object], bytes]


async def _drain(sock: zmq.asyncio.Socket, duration_s: float) -> list[_Frame]:
    """Collect (topic, header_dict, payload) from a SUB socket for duration_s."""
    deadline = asyncio.get_running_loop().time() + duration_s
    items: list[_Frame] = []
    while True:
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            return items
        try:
            topic, hdr, payload = await asyncio.wait_for(
                sock.recv_multipart(), timeout=remaining
            )
        except (TimeoutError, asyncio.TimeoutError):
            return items
        items.append((topic, msgpack.unpackb(hdr), payload))


async def test_publisher_emits_meta_audio_for_each_channel() -> None:
    bind = "tcp://127.0.0.1:55777"
    ctx = zmq.asyncio.Context()
    sub = ctx.socket(zmq.SUB)
    sub.bind(bind)
    sub.setsockopt(zmq.SUBSCRIBE, b"")
    try:
        specs = [
            ChannelSpec(channel_id="t0", rf_freq_hz=100_000_000),
            ChannelSpec(channel_id="t1", rf_freq_hz=100_025_000),
        ]
        async with MultiChannelStressPublisher(
            zmq_connect=bind,
            channel_specs=specs,
            duty_cycle=0.5,
            rng_seed=1,
        ) as pub:
            run_task = asyncio.create_task(pub.run(duration_s=1.2))
            items = await _drain(sub, duration_s=1.5)
            await run_task

        topics = [t.decode() for t, _, _ in items]
        # meta seen for both channels at startup
        assert "meta.t0" in topics
        assert "meta.t1" in topics
        # Plenty of audio frames over 1.2 s @ 20 ms cadence (60 per channel)
        audio_t0 = [t for t in topics if t == "audio.t0"]
        audio_t1 = [t for t in topics if t == "audio.t1"]
        assert len(audio_t0) >= 30  # generous lower bound
        assert len(audio_t1) >= 30

        # sequence numbers strictly increasing per channel
        seqs_t0: list[int] = []
        for topic, hdr, _ in items:
            if topic == b"audio.t0":
                seq = hdr["seq"]
                assert isinstance(seq, int)
                seqs_t0.append(seq)
        assert seqs_t0 == sorted(seqs_t0)
        assert seqs_t0 == list(range(len(seqs_t0)))
    finally:
        sub.close(linger=0)
        ctx.term()


async def test_publisher_run_returns_close_to_duration() -> None:
    bind = "tcp://127.0.0.1:55778"
    ctx = zmq.asyncio.Context()
    sub = ctx.socket(zmq.SUB)
    sub.bind(bind)
    sub.setsockopt(zmq.SUBSCRIBE, b"")
    try:
        specs = [ChannelSpec(channel_id="x", rf_freq_hz=100_000_000)]
        start = asyncio.get_running_loop().time()
        async with MultiChannelStressPublisher(
            zmq_connect=bind, channel_specs=specs, duty_cycle=0.5, rng_seed=1,
        ) as pub:
            await pub.run(duration_s=0.5)
        elapsed = asyncio.get_running_loop().time() - start
        assert 0.4 <= elapsed <= 1.5  # publisher returns close to duration
    finally:
        sub.close(linger=0)
        ctx.term()
