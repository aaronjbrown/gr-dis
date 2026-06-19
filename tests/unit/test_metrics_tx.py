"""Verify new TX-direction metrics are present on BridgeMetrics."""

from __future__ import annotations

from prometheus_client import CollectorRegistry

from gr_dis.metrics import BridgeMetrics


def test_tx_metrics_exist() -> None:
    m = BridgeMetrics(registry=CollectorRegistry())
    assert hasattr(m, "rx_transmitter_pdus_received")
    assert hasattr(m, "rx_signal_pdus_received")
    assert hasattr(m, "tx_audio_frames_published")
    assert hasattr(m, "tx_audio_frames_dropped")
    assert hasattr(m, "rx_pdu_parse_errors")
    assert hasattr(m, "rx_pdu_queue_drops")


def test_tx_audio_frames_dropped_has_reason_label() -> None:
    m = BridgeMetrics(registry=CollectorRegistry())
    m.tx_audio_frames_dropped.labels(channel="ch0", reason="unauthorized").inc()
    m.tx_audio_frames_dropped.labels(channel="ch0", reason="modulation_mismatch").inc()
    m.tx_audio_frames_dropped.labels(channel="ch0", reason="encoding_unsupported").inc()


def test_rx_transmitter_pdus_has_channel_label() -> None:
    m = BridgeMetrics(registry=CollectorRegistry())
    m.rx_transmitter_pdus_received.labels(channel="ch0").inc()
    m.rx_signal_pdus_received.labels(channel="ch0").inc()
    m.tx_audio_frames_published.labels(channel="ch0").inc()
