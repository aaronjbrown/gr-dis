"""ZMQ SUB source block — feeds TX audio from the bridge into a GR flowgraph.

A ZmqTxSource is a gr.sync_block that:
  * subscribes to topic ``tx_audio.<channel_id>`` on the bridge's ZMQ PUB,
  * buffers incoming PCM frames and outputs int16 samples to the GR scheduler,
  * outputs silence (zeros) when the frame queue is empty so the scheduler never stalls,
  * posts pmt.PMT_F on message port "tx_active" when TX becomes active (queue non-empty),
  * posts pmt.PMT_T on "tx_active" after no TX frames for > tx_idle_ms ms.

The PMT_F/PMT_T signals are wired to a blocks.valve on the SoapySDR source output
for half-duplex operation (PMT_F closes the valve, blocking RX; PMT_T opens it).

The gnuradio import is deferred to make_zmq_tx_source so this module imports on
the host without GR — keeping the unit-test suite green.
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Any

import numpy as np
import zmq

if TYPE_CHECKING:  # pragma: no cover
    from gnuradio import gr

logger = logging.getLogger(__name__)

_SOURCE_CLASS: type | None = None


def _build_source_class() -> type:
    global _SOURCE_CLASS
    if _SOURCE_CLASS is not None:
        return _SOURCE_CLASS

    from gnuradio import gr  # noqa: PLC0415

    try:
        from gnuradio import pmt  # noqa: PLC0415
    except ImportError:
        import pmt  # noqa: PLC0415

    class ZmqTxSource(gr.sync_block):  # type: ignore[misc]
        def __init__(
            self,
            endpoint: str,
            channel_id: str,
            *,
            tx_idle_ms: int = 200,
            zmq_context: zmq.Context[Any] | None = None,
        ) -> None:
            gr.sync_block.__init__(
                self,
                name=f"ZmqTxSource[{channel_id}]",
                in_sig=None,
                out_sig=[np.int16],
            )
            self.message_port_register_out(pmt.intern("tx_active"))
            self._endpoint = endpoint
            self._channel_id = channel_id
            self._tx_idle_ms = tx_idle_ms
            self._ctx = zmq_context or zmq.Context()
            self._sock: zmq.Socket[Any] | None = None
            self._buffer = np.empty(0, dtype=np.int16)
            self._is_tx_active = False
            self._last_frame_time = 0.0
            self._rx_gate: Any = None

        def set_rx_gate(self, gate: Any) -> None:
            """Register a blocks.copy gate to mute RX during TX (half-duplex).

            Called by capture.py instead of msg_connect when blocks.valve is
            unavailable.  gate.set_enabled() is thread-safe in GR 3.10.
            """
            self._rx_gate = gate

        def _ensure_socket(self) -> zmq.Socket[Any]:
            if self._sock is None:
                self._sock = self._ctx.socket(zmq.SUB)
                self._sock.setsockopt(zmq.RCVHWM, 200)
                topic = f"tx_audio.{self._channel_id}".encode("ascii")
                self._sock.setsockopt(zmq.SUBSCRIBE, topic)
                self._sock.connect(self._endpoint)
            return self._sock

        def work(
            self,
            input_items: list[np.ndarray],
            output_items: list[np.ndarray],
        ) -> int:
            sock = self._ensure_socket()

            while True:
                try:
                    frames = sock.recv_multipart(flags=zmq.NOBLOCK)
                    if len(frames) == 3:
                        pcm_bytes = frames[2]
                        new_samples = np.frombuffer(pcm_bytes, dtype="<i2").copy()
                        self._buffer = np.concatenate((self._buffer, new_samples))
                        self._last_frame_time = time.monotonic()
                        if not self._is_tx_active:
                            self._is_tx_active = True
                            self.message_port_pub(pmt.intern("tx_active"), pmt.PMT_F)
                            if self._rx_gate is not None:
                                self._rx_gate.set_enabled(False)
                except zmq.Again:
                    break

            if self._is_tx_active:
                idle_ms = (time.monotonic() - self._last_frame_time) * 1000
                if idle_ms > self._tx_idle_ms:
                    self._is_tx_active = False
                    self.message_port_pub(pmt.intern("tx_active"), pmt.PMT_T)
                    if self._rx_gate is not None:
                        self._rx_gate.set_enabled(True)

            out = output_items[0]
            n_out = len(out)
            n_avail = min(len(self._buffer), n_out)
            if n_avail > 0:
                out[:n_avail] = self._buffer[:n_avail]
                self._buffer = self._buffer[n_avail:]
            if n_avail < n_out:
                out[n_avail:] = 0  # silence

            return n_out

        def stop(self) -> bool:
            if self._sock is not None:
                self._sock.close(linger=0)
            return True

    _SOURCE_CLASS = ZmqTxSource
    return _SOURCE_CLASS


def make_zmq_tx_source(
    endpoint: str,
    channel_id: str,
    *,
    tx_idle_ms: int = 200,
    zmq_context: zmq.Context[Any] | None = None,
) -> gr.sync_block:
    """Construct a ZMQ TX source block (GR import deferred to call time)."""
    cls = _build_source_class()
    return cls(
        endpoint,
        channel_id,
        tx_idle_ms=tx_idle_ms,
        zmq_context=zmq_context,
    )
