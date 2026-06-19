"""Build and run a Capture top block.

A Capture process owns one ``gr.top_block`` per CaptureConfig:

    source (SoapySDR or file) → tee to one ModulationChain per channel
                              → one ZmqAudioSink per channel

Each ZmqAudioSink connects (ZMQ-side) to the Bridge's bound SUB endpoint.
``run_capture`` blocks until SIGTERM / SIGINT / KeyboardInterrupt.

All ``gnuradio`` imports are deferred to :func:`build_top_block` so importing
this module on the host (no GR) is safe.
"""

from __future__ import annotations

import logging
import signal
from typing import TYPE_CHECKING, Any

from gr_dis.engine.rx_chains import get_chain
from gr_dis.engine.tx_chains import get_tx_chain
from gr_dis.engine.zmq_sink import make_zmq_audio_sink
from gr_dis.engine.zmq_source import make_zmq_tx_source

if TYPE_CHECKING:  # pragma: no cover
    from gnuradio import gr

    from gr_dis.engine.config import AppConfig, CaptureConfig

logger = logging.getLogger(__name__)


def build_top_block(
    capture: CaptureConfig,
    *,
    source_file: str | None = None,
) -> gr.top_block:
    """Build a top block for one capture.

    Args:
        capture: validated CaptureConfig.
        source_file: optional path to a complex-float-32 IQ file used in place
            of the SoapySDR Source. When set, the file is played back at the
            configured sample_rate using a throttle block and repeats forever.
            This is the testable path that does not require SoapySDR bindings.
    """
    from gnuradio import blocks, gr  # noqa: PLC0415

    tb = gr.top_block(f"Capture[{capture.id}]")

    # Root any Python-defined gr.sync_block subclasses (e.g. ZmqAudioSink) to
    # the top_block's lifetime. tb.connect() only extends the lifetime of the
    # underlying C++ block via shared_ptr; the Python wrapper that owns it is
    # not rooted anywhere. When the loop's `sink` local rebinds on the next
    # iteration the previous Python wrapper would be garbage-collected, and
    # the C++ block's trampoline would dereference a freed PyObject* on the
    # worker thread's first call to start()/work() — manifesting as a SIGSEGV
    # in PyObject_GetAttrString from gr::block_executor::block_executor.
    # GRC-generated code achieves the same effect via `self.block = ...` on
    # the top_block subclass.
    tb._py_blocks = []

    # --- Source ------------------------------------------------------------
    sample_rate_hz = capture.sdr.sample_rate_hz
    center_freq_hz = capture.sdr.center_freq_hz

    if source_file is not None:
        src = blocks.file_source(gr.sizeof_gr_complex, source_file, repeat=True)
        throttle = blocks.throttle(gr.sizeof_gr_complex, sample_rate_hz)
        tb.connect(src, throttle)
        source_out = throttle
        logger.info(
            "capture %s: file source %s @ %.0f Hz (repeat=True)",
            capture.id, source_file, sample_rate_hz,
        )
    else:
        # SoapySDR import is doubly deferred: the toolbox build of GR ships
        # gr-soapy as part of GR 3.10. We avoid importing it unless actually
        # going live.
        from gnuradio import soapy  # noqa: PLC0415

        soapy_args = ",".join(
            [f"driver={capture.sdr.driver}"]
            + [f"{k}={v}" for k, v in capture.sdr.args.items()]
        )
        src = soapy.source(
            soapy_args, "fc32", 1, "", "", [""], [""]
        )
        src.set_sample_rate(0, sample_rate_hz)
        src.set_frequency(0, center_freq_hz)
        if isinstance(capture.sdr.gain_db, dict):
            for stage, g in capture.sdr.gain_db.items():
                src.set_gain(0, stage, g)
        else:
            src.set_gain(0, capture.sdr.gain_db)
        if capture.sdr.bandwidth_hz:
            src.set_bandwidth(0, capture.sdr.bandwidth_hz)
        if capture.sdr.antenna:
            src.set_antenna(0, capture.sdr.antenna)
        source_out = src
        logger.info(
            "capture %s: SoapySDR driver=%s @ %.0f Hz, center %.0f Hz",
            capture.id, capture.sdr.driver, sample_rate_hz, center_freq_hz,
        )

    # --- Half-duplex gate (inserted before RX chains when duplex == half) ---
    # blocks.valve is absent from GR 3.10; use blocks.copy whose set_enabled()
    # is thread-safe so ZmqTxSource can call it directly instead of msg_connect.
    # Skip entirely for file-source playback — no physical duplex constraint.
    from gr_dis.engine.config import DuplexMode  # noqa: PLC0415
    has_tx = any(ch.tx_enabled for ch in capture.channels)
    valve = None
    if has_tx and capture.sdr.duplex == DuplexMode.half and source_file is None:
        valve = blocks.copy(gr.sizeof_gr_complex)
        valve.set_enabled(True)
        tb.connect(source_out, valve)
        source_out = valve
        logger.info("capture %s: half-duplex gate (blocks.copy) inserted on RX source", capture.id)

    # --- Per-channel chains + ZMQ sinks ------------------------------------
    for channel in capture.channels:
        chain_cls = get_chain(channel.chain)
        chain = chain_cls().build(
            input_sample_rate_hz=sample_rate_hz,
            channel_bandwidth_hz=float(channel.bandwidth_hz),
            rf_offset_hz=float(channel.rf_freq_hz) - center_freq_hz,
            chain_config=channel.chain_config,
        )
        sink = make_zmq_audio_sink(
            endpoint=capture.zmq_connect,
            channel_id=channel.id,
            chain_name=channel.chain,
            rf_freq_hz=int(channel.rf_freq_hz),
            channel_bandwidth_hz=int(channel.bandwidth_hz),
            chain_config=channel.chain_config,
        )
        tb._py_blocks.append(sink)  # see top_block construction above
        tb.connect(source_out, chain)
        tb.connect(chain, sink)
        logger.info(
            "capture %s: channel %s chain=%s rf=%d Hz bw=%d Hz → %s",
            capture.id, channel.id, channel.chain,
            channel.rf_freq_hz, channel.bandwidth_hz, capture.zmq_connect,
        )

    # --- TX path (live SDR only; not used with file-source playback) ---
    if has_tx and source_file is None:
        tx_sources = _build_tx_path(tb, capture, sample_rate_hz, center_freq_hz)
        if valve is not None:
            for tx_src in tx_sources:
                tx_src.set_rx_gate(valve)
            logger.info(
                "capture %s: %d TX source(s) wired to half-duplex gate",
                capture.id, len(tx_sources),
            )
    elif has_tx:
        logger.info("capture %s: TX path skipped (file source)", capture.id)

    return tb


def _build_tx_path(
    tb: gr.top_block,
    capture: CaptureConfig,
    sample_rate_hz: float,
    center_freq_hz: float,
) -> list[Any]:
    """Wire TX chains + ZmqTxSource + SoapySDR sink for each tx_enabled channel."""
    from gnuradio import soapy  # noqa: PLC0415

    tx_sources = []

    for channel in capture.channels:
        if not channel.tx_enabled:
            continue

        chain_cls = get_tx_chain(channel.chain)
        chain = chain_cls().build_tx(
            output_sample_rate_hz=sample_rate_hz,
            rf_offset_hz=float(channel.rf_freq_hz) - center_freq_hz,
            channel_bandwidth_hz=float(channel.bandwidth_hz),
            chain_config=channel.chain_config,
        )

        source = make_zmq_tx_source(
            capture.zmq_tx_connect,  # type: ignore[arg-type]
            channel.id,
        )
        tb._py_blocks.append(source)

        soapy_args = ",".join(
            [f"driver={capture.sdr.driver}"]
            + [f"{k}={v}" for k, v in capture.sdr.args.items()]
        )
        sink = soapy.sink(soapy_args, "fc32", 1, "", "", [""], [""])
        sink.set_sample_rate(0, sample_rate_hz)
        sink.set_frequency(0, center_freq_hz)
        if isinstance(capture.sdr.gain_db, dict):
            for stage, g in capture.sdr.gain_db.items():
                sink.set_gain(0, stage, g)
        else:
            sink.set_gain(0, capture.sdr.gain_db)
        if capture.sdr.bandwidth_hz:
            sink.set_bandwidth(0, capture.sdr.bandwidth_hz)
        if capture.sdr.antenna:
            sink.set_antenna(0, capture.sdr.antenna)

        tb.connect(source, chain, sink)
        tx_sources.append(source)

        logger.info(
            "capture %s: TX channel %s chain=%s rf=%d Hz → SoapySDR sink",
            capture.id, channel.id, channel.chain, channel.rf_freq_hz,
        )

    return tx_sources


def run_capture(
    config: AppConfig,
    capture_id: str | None = None,
    *,
    source_file: str | None = None,
) -> int:
    """Build and run a capture top block until interrupted.

    Args:
        config: validated AppConfig.
        capture_id: which capture to run. Defaults to the first one.
        source_file: optional IQ file source (testing/offline).

    Returns:
        Exit code suitable for ``sys.exit()``: 0 on clean stop, non-zero on
        flowgraph error.
    """
    if capture_id is None:
        capture = config.captures[0]
    else:
        matches = [c for c in config.captures if c.id == capture_id]
        if not matches:
            ids = [c.id for c in config.captures]
            raise KeyError(f"capture {capture_id!r} not found; available: {ids}")
        capture = matches[0]

    tb = build_top_block(capture, source_file=source_file)

    stopped = False

    def _handle_signal(signum: int, _frame: object) -> None:
        nonlocal stopped
        if stopped:
            return
        stopped = True
        logger.info("signal %d received; stopping flowgraph", signum)
        tb.stop()

    old_sigterm = signal.signal(signal.SIGTERM, _handle_signal)
    old_sigint = signal.signal(signal.SIGINT, _handle_signal)

    try:
        tb.start()
        tb.wait()
        return 0
    except Exception:
        logger.exception("capture %s failed", capture.id)
        return 1
    finally:
        signal.signal(signal.SIGTERM, old_sigterm)
        signal.signal(signal.SIGINT, old_sigint)
