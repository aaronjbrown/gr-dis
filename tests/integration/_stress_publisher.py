"""Multi-channel stress publisher.

Drives N synthetic channels through a randomized IDLE/TALK FSM, sharing one
ZMQ PUB socket. Module-public helpers (FSMState, compute_idle_range,
iter_dwell_sequence) are pure and unit-testable without ZMQ.

The publisher class (MultiChannelStressPublisher) handles the I/O.
"""

from __future__ import annotations

import asyncio
import enum
import random
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

import msgpack
import zmq
import zmq.asyncio

from tests.integration._synthetic_publisher import silence_pcm, sine_pcm

if TYPE_CHECKING:
    from collections.abc import Iterator

# Talk dwell distribution is fixed: voice transmissions are 1–8 s, mean 4.5 s.
_TALK_DWELL_LO = 1.0
_TALK_DWELL_HI = 8.0
_TALK_DWELL_MEAN = (_TALK_DWELL_LO + _TALK_DWELL_HI) / 2

# Idle dwell spread relative to its mean. mean_idle * 0.3 .. mean_idle * 1.7.
_IDLE_SPREAD_LO = 0.3
_IDLE_SPREAD_HI = 1.7


class FSMState(enum.Enum):
    IDLE = "idle"
    TALK = "talk"


def compute_idle_range(duty_cycle: float) -> tuple[float, float]:
    """Return (lo, hi) for the uniform-distribution idle dwell.

    mean_idle is derived from the duty-cycle formula:
        duty = mean_talk / (mean_talk + mean_idle)
        mean_idle = mean_talk * (1 - duty) / duty
    Spread is mean_idle * 0.3 .. mean_idle * 1.7.
    """
    if not 0.0 < duty_cycle < 1.0:
        raise ValueError(
            f"duty_cycle must be in (0, 1) exclusive, got {duty_cycle!r}"
        )
    mean_idle = _TALK_DWELL_MEAN * (1.0 - duty_cycle) / duty_cycle
    return mean_idle * _IDLE_SPREAD_LO, mean_idle * _IDLE_SPREAD_HI


def iter_dwell_sequence(
    *, seed: int, duty_cycle: float, n: int,
) -> Iterator[tuple[FSMState, float]]:
    """Deterministically generate the first `n` (state, dwell_seconds) pairs.

    The first item is always IDLE with dwell uniform in [0, mean_idle]
    (initial offset, so multi-channel runs don't synchronize). Subsequent
    items alternate TALK / IDLE; TALK dwells are U[1.0, 8.0]; non-initial
    IDLE dwells are U[idle_lo, idle_hi] from compute_idle_range.
    """
    rng = random.Random(seed)
    idle_lo, idle_hi = compute_idle_range(duty_cycle)
    mean_idle = (idle_lo + idle_hi) / 2
    # initial IDLE offset
    yield FSMState.IDLE, rng.uniform(0.0, mean_idle)
    emitted = 1
    while emitted < n:
        yield FSMState.TALK, rng.uniform(_TALK_DWELL_LO, _TALK_DWELL_HI)
        emitted += 1
        if emitted >= n:
            return
        yield FSMState.IDLE, rng.uniform(idle_lo, idle_hi)
        emitted += 1


@dataclass(frozen=True)
class ChannelSpec:
    channel_id: str
    rf_freq_hz: int


class MultiChannelStressPublisher:
    """Drive N channels through randomized IDLE/TALK FSMs over one ZMQ PUB."""

    def __init__(
        self,
        zmq_connect: str,
        channel_specs: list[ChannelSpec],
        *,
        duty_cycle: float = 0.25,
        frame_period_s: float = 0.02,
        rng_seed: int = 1,
    ) -> None:
        if not channel_specs:
            raise ValueError("channel_specs must be non-empty")
        # Validate duty_cycle eagerly via compute_idle_range
        compute_idle_range(duty_cycle)
        self._zmq_connect = zmq_connect
        self._specs = channel_specs
        self._duty_cycle = duty_cycle
        self._frame_period_s = frame_period_s
        self._rng_seed = rng_seed
        self._ctx: zmq.asyncio.Context | None = None
        self._pub: zmq.asyncio.Socket | None = None
        # Per-channel sequence counters (audio frames are seq-numbered).
        self._seq: dict[str, int] = {s.channel_id: 0 for s in channel_specs}

    async def __aenter__(self) -> MultiChannelStressPublisher:
        self._ctx = zmq.asyncio.Context()
        try:
            self._pub = self._ctx.socket(zmq.PUB)
            # Generous send HWM so the publisher never blocks at our target rate.
            self._pub.setsockopt(zmq.SNDHWM, 10000)
            self._pub.connect(self._zmq_connect)
            # ZMQ slow-joiner: give the SUB time to subscribe.
            await asyncio.sleep(0.15)
        except BaseException:
            # Roll back partial setup so we don't leak the ZMQ context.
            await self.__aexit__(None, None, None)
            raise
        return self

    async def __aexit__(self, *_exc: object) -> None:
        if self._pub is not None:
            self._pub.close(linger=200)
            self._pub = None
        if self._ctx is not None:
            self._ctx.term()
            self._ctx = None

    async def run(self, duration_s: float) -> None:
        """Run all per-channel FSMs concurrently for duration_s seconds."""
        if self._pub is None:
            raise RuntimeError("Publisher not entered; use 'async with'")
        deadline = asyncio.get_running_loop().time() + duration_s
        tasks = [
            asyncio.create_task(
                self._run_channel(spec, deadline, self._rng_seed + i)
            )
            for i, spec in enumerate(self._specs)
        ]
        try:
            await asyncio.gather(*tasks)
        except asyncio.CancelledError:
            for t in tasks:
                t.cancel()
            raise

    async def _run_channel(
        self, spec: ChannelSpec, deadline: float, channel_seed: int,
    ) -> None:
        # One meta at startup.
        await self._send_meta(spec, active=True)

        # iter_dwell_sequence is the authoritative FSM dwell generator.
        # Pass a huge n so the iterator is effectively infinite for our purposes.
        dwell_iter = iter_dwell_sequence(
            seed=channel_seed, duty_cycle=self._duty_cycle, n=10**9,
        )
        state, state_dwell = next(dwell_iter)
        state_started = asyncio.get_running_loop().time()

        silence = silence_pcm()
        voice = sine_pcm()

        while True:
            now = asyncio.get_running_loop().time()
            if now >= deadline:
                return

            # If dwell expired, transition.
            if now - state_started >= state_dwell:
                # Transition: the iterator alternates IDLE/TALK/IDLE/... after
                # the initial offset, so next() gives us the next state+dwell.
                state, state_dwell = next(dwell_iter)
                event = "squelch_open" if state == FSMState.TALK else "squelch_close"
                await self._send_event(spec, event)
                # Reset state_started with a fresh time reading AFTER the await
                # so dwell drift doesn't accumulate over many transitions.
                state_started = asyncio.get_running_loop().time()

            pcm = voice if state == FSMState.TALK else silence
            await self._send_audio(spec, pcm, squelch=(state == FSMState.TALK))

            # Sleep to next frame boundary, but never past the deadline.
            sleep_for = min(self._frame_period_s, max(0.0, deadline - now))
            if sleep_for <= 0:
                return
            await asyncio.sleep(sleep_for)

    async def _send_meta(self, spec: ChannelSpec, *, active: bool) -> None:
        assert self._pub is not None
        hdr = msgpack.packb(
            {
                "v": 1,
                "type": "meta",
                "ts_ns": time.time_ns(),
                "chain": "nbfm",
                "rf_freq_hz": spec.rf_freq_hz,
                "channel_bandwidth_hz": 25_000,
                "active": active,
                "chain_config": {},
            },
            use_bin_type=True,
        )
        topic = f"meta.{spec.channel_id}".encode()
        await self._pub.send_multipart([topic, hdr, b""])

    async def _send_audio(
        self, spec: ChannelSpec, pcm: bytes, *, squelch: bool,
    ) -> None:
        assert self._pub is not None
        seq = self._seq[spec.channel_id]
        self._seq[spec.channel_id] += 1
        n = len(pcm) // 2
        ts_ns = time.time_ns()
        hdr = msgpack.packb(
            {
                "v": 1,
                "type": "audio",
                "seq": seq,
                "ts_ns": ts_ns,
                "host_ts_ns": ts_ns,
                "n": n,
                "sr": 8000,
                "fmt": "pcm_s16le",
                "squelch": squelch,
                "rssi_dbfs": None,
            },
            use_bin_type=True,
        )
        topic = f"audio.{spec.channel_id}".encode()
        await self._pub.send_multipart([topic, hdr, pcm])

    async def _send_event(self, spec: ChannelSpec, name: str) -> None:
        assert self._pub is not None
        hdr = msgpack.packb(
            {
                "v": 1,
                "type": "event",
                "ts_ns": time.time_ns(),
                "name": name,
                "data": {},
            },
            use_bin_type=True,
        )
        topic = f"event.{spec.channel_id}".encode()
        await self._pub.send_multipart([topic, hdr, b""])
