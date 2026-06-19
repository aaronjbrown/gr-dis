"""ZMQ PUB wrapper — publishes decoded PCM on tx_audio.<channel_id> topics."""

from __future__ import annotations

import logging
import time

import msgpack
import zmq

logger = logging.getLogger(__name__)

_PROTOCOL_VERSION = 1
_SAMPLE_RATE_HZ = 8_000


class TxPublisher:
    """Binds a ZMQ PUB socket and publishes PCM frames to GR capture processes."""

    def __init__(self, zmq_bind: str) -> None:
        self._ctx = zmq.Context()
        self._sock = self._ctx.socket(zmq.PUB)
        self._sock.setsockopt(zmq.SNDHWM, 200)
        self._sock.bind(zmq_bind)
        self._seq = 0

    def publish(self, channel_id: str, pcm_bytes: bytes) -> None:
        """Publish one PCM frame on topic ``tx_audio.<channel_id>``."""
        n_samples = len(pcm_bytes) // 2
        header = msgpack.packb(
            {
                "v": _PROTOCOL_VERSION,
                "type": "tx_audio",
                "seq": self._seq,
                "ts_ns": time.monotonic_ns(),
                "n": n_samples,
                "sr": _SAMPLE_RATE_HZ,
                "fmt": "pcm_s16le",
            },
            use_bin_type=True,
        )
        topic = f"tx_audio.{channel_id}".encode("ascii")
        try:
            self._sock.send_multipart([topic, header, pcm_bytes], flags=zmq.NOBLOCK)
        except zmq.Again:
            logger.warning("zmq HWM: dropped TX audio frame for channel %s", channel_id)
        self._seq += 1

    def close(self) -> None:
        self._sock.close(linger=0)
