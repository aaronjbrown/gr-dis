"""Synthetic ZMQ publisher that mimics the GR Capture side.

Sends the wire protocol from 03-wire-protocol.md using scripted events and
a programmatically-generated sine-wave audio signal.
"""

from __future__ import annotations

import asyncio
import math
import struct
import time

import msgpack
import zmq
import zmq.asyncio


def sine_pcm(n_samples: int = 160, freq_hz: float = 1000.0, sr_hz: float = 8000.0) -> bytes:
    """Generate n_samples of signed 16-bit PCM at freq_hz."""
    samples = [
        int(16384 * math.sin(2 * math.pi * freq_hz * i / sr_hz))
        for i in range(n_samples)
    ]
    return struct.pack(f"<{n_samples}h", *samples)


def silence_pcm(n_samples: int = 160) -> bytes:
    return bytes(n_samples * 2)


class SyntheticPublisher:
    """ZMQ PUB that sends scripted audio/meta/event messages."""

    def __init__(self, zmq_connect: str, channel_ids: list[str]) -> None:
        self._zmq_connect = zmq_connect
        self._channel_ids = channel_ids
        self._ctx = zmq.asyncio.Context.instance()
        self._pub: zmq.asyncio.Socket | None = None
        self._seq: dict[str, int] = {ch: 0 for ch in channel_ids}

    async def __aenter__(self) -> SyntheticPublisher:
        self._pub = self._ctx.socket(zmq.PUB)
        self._pub.connect(self._zmq_connect)
        # ZMQ slow-joiner delay: give the SUB socket time to subscribe
        await asyncio.sleep(0.15)
        return self

    async def __aexit__(self, *_: object) -> None:
        if self._pub is not None:
            self._pub.close(linger=0)
            self._pub = None

    def _now_ns(self) -> int:
        return time.time_ns()

    async def send_meta(
        self,
        channel_id: str,
        rf_freq_hz: int,
        bandwidth_hz: int = 25_000,
        chain: str = "nbfm",
        active: bool = True,
    ) -> None:
        assert self._pub is not None
        header = msgpack.packb(
            {
                "v": 1,
                "type": "meta",
                "ts_ns": self._now_ns(),
                "chain": chain,
                "rf_freq_hz": rf_freq_hz,
                "channel_bandwidth_hz": bandwidth_hz,
                "active": active,
                "chain_config": {},
            },
            use_bin_type=True,
        )
        topic = f"meta.{channel_id}".encode()
        await self._pub.send_multipart([topic, header, b""])

    async def send_audio(
        self,
        channel_id: str,
        pcm: bytes,
        squelch: bool,
        rssi_dbfs: float | None = None,
    ) -> None:
        assert self._pub is not None
        seq = self._seq[channel_id]
        self._seq[channel_id] += 1
        n = len(pcm) // 2
        header = msgpack.packb(
            {
                "v": 1,
                "type": "audio",
                "seq": seq,
                "ts_ns": self._now_ns(),
                "host_ts_ns": self._now_ns(),
                "n": n,
                "sr": 8000,
                "fmt": "pcm_s16le",
                "squelch": squelch,
                "rssi_dbfs": rssi_dbfs,
            },
            use_bin_type=True,
        )
        topic = f"audio.{channel_id}".encode()
        await self._pub.send_multipart([topic, header, pcm])

    async def send_event(
        self,
        channel_id: str,
        name: str,
        data: dict[str, object] | None = None,
    ) -> None:
        assert self._pub is not None
        header = msgpack.packb(
            {
                "v": 1,
                "type": "event",
                "ts_ns": self._now_ns(),
                "name": name,
                "data": data or {},
            },
            use_bin_type=True,
        )
        topic = f"event.{channel_id}".encode()
        await self._pub.send_multipart([topic, header, b""])

    async def run_voice_scenario(
        self,
        channel_id: str,
        rf_freq_hz: int,
        n_idle_before: int = 3,
        n_voice: int = 8,
        n_idle_after: int = 3,
        frame_delay_s: float = 0.02,
    ) -> None:
        """Run: meta → idle frames → squelch_open → voice frames → squelch_close → idle."""
        silence = silence_pcm()
        voice = sine_pcm()

        await self.send_meta(channel_id, rf_freq_hz)

        for _ in range(n_idle_before):
            await self.send_audio(channel_id, silence, squelch=False)
            await asyncio.sleep(frame_delay_s)

        await self.send_event(channel_id, "squelch_open")

        for _ in range(n_voice):
            await self.send_audio(channel_id, voice, squelch=True)
            await asyncio.sleep(frame_delay_s)

        await self.send_event(channel_id, "squelch_close")

        for _ in range(n_idle_after):
            await self.send_audio(channel_id, silence, squelch=False)
            await asyncio.sleep(frame_delay_s)
