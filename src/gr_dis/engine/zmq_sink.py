"""ZMQ PUB sink — bridges the GR audio stream to the wire protocol.

A :class:`ZmqAudioSink` is a ``gr.sync_block`` that:

  * consumes mono int16 PCM at 8 kHz,
  * buffers it into 20 ms frames (160 samples each),
  * publishes each frame on topic ``audio.<channel_id>`` with a MessagePack
    header per ``planning/03-wire-protocol.md``,
  * emits periodic ``meta.<channel_id>`` heartbeats,
  * emits ``event.<channel_id>`` ``squelch_open`` / ``squelch_close`` messages
    on per-frame RMS threshold crossings.

The ``gnuradio`` import is deferred to :func:`make_zmq_audio_sink` so this
module imports cleanly without GR (unit-test suite friendly).
"""

from __future__ import annotations

import contextlib
import logging
import time
from typing import TYPE_CHECKING, Any

import msgpack
import numpy as np
import zmq

if TYPE_CHECKING:  # pragma: no cover
    from gnuradio import gr

logger = logging.getLogger(__name__)


FRAME_SAMPLES = 160        # 20 ms @ 8 kHz
SAMPLE_RATE_HZ = 8_000
META_HEARTBEAT_S = 5.0
PROTOCOL_VERSION = 1

# Post-demod RMS threshold (int16 units) used to derive the ``squelch`` flag
# for downstream DIS Transmit State decisions. The chain's own pwr_squelch_cc
# mutes audio in the IQ domain, so muted frames land near zero RMS; a small
# but non-trivial threshold reliably distinguishes voice from silence.
_DEFAULT_SQUELCH_RMS = 50.0


_SINK_CLASS: type | None = None


def _build_sink_class() -> type:
    """Build the ZmqAudioSink class exactly once.

    Defining the class inside the factory function created a *new* GR block
    subclass per call, which churns class objects unnecessarily. The cached
    singleton is purely cosmetic — the multi-sink segfault that originally
    motivated this was actually a Python-wrapper lifetime issue in capture.py
    (the C++ block held a dangling PyObject* once the Python wrapper was
    GC'd); see the keepalive comment in ``engine/capture.py``.
    """
    global _SINK_CLASS
    if _SINK_CLASS is not None:
        return _SINK_CLASS

    from gnuradio import gr  # noqa: PLC0415

    class ZmqAudioSink(gr.sync_block):  # type: ignore[misc]
        def __init__(
            self,
            endpoint: str,
            channel_id: str,
            *,
            chain_name: str,
            rf_freq_hz: int,
            channel_bandwidth_hz: int,
            chain_config: dict[str, Any] | None,
            zmq_context: zmq.Context[Any] | None,
            squelch_rms_threshold: float,
        ) -> None:
            gr.sync_block.__init__(
                self,
                name=f"ZmqAudioSink[{channel_id}]",
                in_sig=[np.int16],
                out_sig=None,
            )
            self._endpoint = endpoint
            self._channel_id = channel_id
            self._chain_name = chain_name
            self._rf_freq_hz = rf_freq_hz
            self._channel_bandwidth_hz = channel_bandwidth_hz
            self._chain_config = dict(chain_config or {})
            self._squelch_rms_threshold = squelch_rms_threshold

            # Create the Context in the main (construction) thread; only the
            # socket itself is created/used in the GR worker thread. This
            # avoids a libzmq io-thread bring-up under the GIL in a GR worker.
            self._ctx = zmq_context or zmq.Context()

            self._sock: zmq.Socket[Any] | None = None
            self._seq = 0
            self._buffer = np.empty(0, dtype=np.int16)
            self._squelch_open = False
            self._last_meta_mono = 0.0

        def set_rf_freq_hz(self, rf_freq_hz: int) -> None:
            self._rf_freq_hz = int(rf_freq_hz)
            if self._sock is not None:
                self._send_meta()

        def _ensure_socket(self) -> zmq.Socket[Any]:
            if self._sock is None:
                self._sock = self._ctx.socket(zmq.PUB)
                self._sock.setsockopt(zmq.SNDHWM, 100)
                self._sock.connect(self._endpoint)
                # ZMQ slow-joiner: tiny pause so the bridge SUB has time to
                # register the subscription before the first audio frame is
                # sent. This runs in the work thread, not at construction.
                time.sleep(0.05)
                self._send_meta()
            return self._sock

        def work(
            self,
            input_items: list[np.ndarray],
            output_items: list[np.ndarray],
        ) -> int:
            samples: np.ndarray = input_items[0]
            n_in = len(samples)
            if n_in == 0:
                return 0

            self._ensure_socket()
            self._buffer = np.concatenate((self._buffer, samples))

            while len(self._buffer) >= FRAME_SAMPLES:
                frame = self._buffer[:FRAME_SAMPLES]
                self._buffer = self._buffer[FRAME_SAMPLES:]
                self._emit_frame(frame)

            now_mono = time.monotonic()
            if now_mono - self._last_meta_mono >= META_HEARTBEAT_S:
                self._send_meta()

            return n_in

        def _emit_frame(self, frame: np.ndarray) -> None:
            rms = float(np.sqrt(np.mean(frame.astype(np.float64) ** 2)))
            squelch_now = rms > self._squelch_rms_threshold

            if squelch_now != self._squelch_open:
                self._squelch_open = squelch_now
                self._send_event(
                    "squelch_open" if squelch_now else "squelch_close",
                    {"rms": rms},
                )

            header = msgpack.packb(
                {
                    "v": PROTOCOL_VERSION,
                    "type": "audio",
                    "seq": self._seq,
                    "ts_ns": time.monotonic_ns(),
                    "host_ts_ns": time.time_ns(),
                    "n": FRAME_SAMPLES,
                    "sr": SAMPLE_RATE_HZ,
                    "fmt": "pcm_s16le",
                    "squelch": squelch_now,
                    "rssi_dbfs": None,
                },
                use_bin_type=True,
            )
            payload = frame.tobytes()
            topic = f"audio.{self._channel_id}".encode("ascii")
            assert self._sock is not None
            try:
                self._sock.send_multipart([topic, header, payload], flags=zmq.NOBLOCK)
            except zmq.Again:
                logger.warning(
                    "zmq HWM hit on channel %s; dropping seq %d",
                    self._channel_id, self._seq,
                )
            self._seq += 1

        def _send_meta(self) -> None:
            header = msgpack.packb(
                {
                    "v": PROTOCOL_VERSION,
                    "type": "meta",
                    "ts_ns": time.monotonic_ns(),
                    "chain": self._chain_name,
                    "rf_freq_hz": self._rf_freq_hz,
                    "channel_bandwidth_hz": self._channel_bandwidth_hz,
                    "active": True,
                    "chain_config": self._chain_config,
                },
                use_bin_type=True,
            )
            topic = f"meta.{self._channel_id}".encode("ascii")
            if self._sock is not None:
                with contextlib.suppress(zmq.Again):
                    self._sock.send_multipart([topic, header, b""], flags=zmq.NOBLOCK)
            self._last_meta_mono = time.monotonic()

        def _send_event(self, name: str, data: dict[str, Any]) -> None:
            header = msgpack.packb(
                {
                    "v": PROTOCOL_VERSION,
                    "type": "event",
                    "ts_ns": time.monotonic_ns(),
                    "name": name,
                    "data": data,
                },
                use_bin_type=True,
            )
            topic = f"event.{self._channel_id}".encode("ascii")
            if self._sock is not None:
                with contextlib.suppress(zmq.Again):
                    self._sock.send_multipart([topic, header, b""], flags=zmq.NOBLOCK)

        def stop(self) -> bool:
            header = msgpack.packb(
                {
                    "v": PROTOCOL_VERSION,
                    "type": "meta",
                    "ts_ns": time.monotonic_ns(),
                    "chain": self._chain_name,
                    "rf_freq_hz": self._rf_freq_hz,
                    "channel_bandwidth_hz": self._channel_bandwidth_hz,
                    "active": False,
                    "chain_config": self._chain_config,
                },
                use_bin_type=True,
            )
            topic = f"meta.{self._channel_id}".encode("ascii")
            if self._sock is not None:
                with contextlib.suppress(zmq.Again):
                    self._sock.send_multipart([topic, header, b""], flags=zmq.NOBLOCK)
                self._sock.close(linger=100)
            return True

    _SINK_CLASS = ZmqAudioSink
    return _SINK_CLASS


def make_zmq_audio_sink(
    endpoint: str,
    channel_id: str,
    *,
    chain_name: str,
    rf_freq_hz: int,
    channel_bandwidth_hz: int,
    chain_config: dict[str, Any] | None = None,
    zmq_context: zmq.Context[Any] | None = None,
    squelch_rms_threshold: float = _DEFAULT_SQUELCH_RMS,
) -> gr.sync_block:
    """Construct a ZMQ audio sink block (GR import deferred to call time)."""
    cls = _build_sink_class()
    return cls(
        endpoint,
        channel_id,
        chain_name=chain_name,
        rf_freq_hz=rf_freq_hz,
        channel_bandwidth_hz=channel_bandwidth_hz,
        chain_config=chain_config,
        zmq_context=zmq_context,
        squelch_rms_threshold=squelch_rms_threshold,
    )
