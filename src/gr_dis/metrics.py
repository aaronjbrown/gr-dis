"""Prometheus metrics for the gr-dis bridge (FR-12)."""

from __future__ import annotations

import logging
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from prometheus_client import (
    CollectorRegistry,
    Counter,
    Histogram,
    generate_latest,
)

logger = logging.getLogger(__name__)


class BridgeMetrics:
    """All Prometheus metrics for one Bridge instance.

    Pass an isolated ``CollectorRegistry()`` in tests to avoid duplicate-metric
    errors between test runs.  In production, omit the argument and use the
    default global registry.
    """

    def __init__(self, registry: CollectorRegistry | None = None) -> None:
        from prometheus_client import REGISTRY

        r = registry if registry is not None else REGISTRY

        self.signal_pdus_sent = Counter(
            "gr_dis_signal_pdus_sent_total",
            "Signal PDUs sent to the DIS multicast group",
            ["channel"],
            registry=r,
        )
        self.transmitter_pdus_sent = Counter(
            "gr_dis_transmitter_pdus_sent_total",
            "Transmitter PDUs sent to the DIS multicast group",
            ["radio"],
            registry=r,
        )
        self.audio_frames_received = Counter(
            "gr_dis_audio_frames_received_total",
            "Audio frames received from ZMQ",
            ["channel"],
            registry=r,
        )
        self.audio_frames_dropped = Counter(
            "gr_dis_audio_frames_dropped_total",
            "Audio frames dropped before PDU emission",
            ["channel", "reason"],
            registry=r,
        )
        self.zmq_hwm_drops = Counter(
            "gr_dis_zmq_hwm_drops_total",
            "Estimated ZMQ high-water-mark drops (seq-gap based)",
            ["channel"],
            registry=r,
        )
        self.e2e_latency = Histogram(
            "gr_dis_e2e_latency_seconds",
            "End-to-end latency: audio sample captured → Signal PDU on wire",
            buckets=[0.005, 0.01, 0.02, 0.05, 0.1, 0.25, 0.5, 1.0, 2.0],
            registry=r,
        )
        self.rx_transmitter_pdus_received = Counter(
            "gr_dis_rx_transmitter_pdus_received_total",
            "DIS Transmitter PDUs received from multicast (TX direction)",
            ["channel"],
            registry=r,
        )
        self.rx_signal_pdus_received = Counter(
            "gr_dis_rx_signal_pdus_received_total",
            "DIS Signal PDUs received from multicast (TX direction)",
            ["channel"],
            registry=r,
        )
        self.tx_audio_frames_published = Counter(
            "gr_dis_tx_audio_frames_published_total",
            "PCM audio frames published to ZMQ TX PUB",
            ["channel"],
            registry=r,
        )
        self.tx_audio_frames_dropped = Counter(
            "gr_dis_tx_audio_frames_dropped_total",
            "TX audio frames dropped before ZMQ publish",
            ["channel", "reason"],
            registry=r,
        )
        self.rx_pdu_parse_errors = Counter(
            "gr_dis_rx_pdu_parse_errors_total",
            "Incoming DIS PDUs that failed to parse",
            registry=r,
        )
        self.rx_pdu_queue_drops = Counter(
            "gr_dis_rx_pdu_queue_drops_total",
            "Incoming DIS PDUs dropped due to ingest queue full",
            registry=r,
        )
        self._registry = r
        self._dead_channels: set[str] = set()
        self._dead_channels_lock = threading.Lock()

    def mark_channel_dead(self, channel_id: str) -> None:
        with self._dead_channels_lock:
            self._dead_channels.add(channel_id)

    def all_channels_alive(self) -> bool:
        with self._dead_channels_lock:
            return not self._dead_channels


def start_metrics_server(metrics: BridgeMetrics, bind: str) -> HTTPServer:
    """Start Prometheus + /healthz HTTP server in a daemon thread."""
    host, port_str = bind.rsplit(":", 1)
    port = int(port_str)
    registry = metrics._registry

    class _Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            if self.path == "/metrics":
                data = generate_latest(registry)
                self.send_response(200)
                self.send_header("Content-Type", "text/plain; version=0.0.4")
                self.end_headers()
                self.wfile.write(data)
            elif self.path == "/healthz":
                if metrics.all_channels_alive():
                    self.send_response(200)
                    self.end_headers()
                    self.wfile.write(b"OK\n")
                else:
                    self.send_response(503)
                    self.end_headers()
                    with metrics._dead_channels_lock:
                        dead = set(metrics._dead_channels)
                    dead_str = ", ".join(sorted(dead))
                    self.wfile.write(f"degraded: {dead_str}\n".encode())
            else:
                self.send_response(404)
                self.end_headers()

        def log_message(self, fmt: str, *args: object) -> None:
            pass

    server = HTTPServer((host, port), _Handler)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    logger.info("metrics server listening on http://%s:%d/metrics", host, port)
    return server
