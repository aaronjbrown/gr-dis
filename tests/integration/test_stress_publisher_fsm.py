"""Unit tests for the stress harness — pure logic, no ZMQ/HTTP."""

from __future__ import annotations

import math

from tests.integration._stress_publisher import (
    FSMState,
    compute_idle_range,
    iter_dwell_sequence,
)
from tests.integration._stress_reporting import build_report, parse_prometheus

SAMPLE_METRICS = """\
# HELP gr_dis_signal_pdus_sent_total Signal PDUs sent
# TYPE gr_dis_signal_pdus_sent_total counter
gr_dis_signal_pdus_sent_total{channel="ch_0"} 100.0
gr_dis_signal_pdus_sent_total{channel="ch_1"} 250.0
# HELP gr_dis_zmq_hwm_drops_total ZMQ HWM drops
# TYPE gr_dis_zmq_hwm_drops_total counter
gr_dis_zmq_hwm_drops_total{channel="ch_0"} 0.0
gr_dis_zmq_hwm_drops_total{channel="ch_1"} 0.0
# HELP gr_dis_audio_frames_dropped_total Audio frames dropped
# TYPE gr_dis_audio_frames_dropped_total counter
# HELP gr_dis_e2e_latency_seconds E2E latency
# TYPE gr_dis_e2e_latency_seconds histogram
gr_dis_e2e_latency_seconds_bucket{le="0.005"} 10.0
gr_dis_e2e_latency_seconds_bucket{le="0.01"} 50.0
gr_dis_e2e_latency_seconds_bucket{le="0.25"} 990.0
gr_dis_e2e_latency_seconds_bucket{le="0.5"} 999.0
gr_dis_e2e_latency_seconds_bucket{le="1.0"} 1000.0
gr_dis_e2e_latency_seconds_bucket{le="+Inf"} 1000.0
gr_dis_e2e_latency_seconds_count 1000.0
gr_dis_e2e_latency_seconds_sum 50.0
"""


def test_parse_prometheus_sums_counter_labels() -> None:
    result = parse_prometheus(SAMPLE_METRICS)
    assert result["gr_dis_signal_pdus_sent_total"] == 350.0  # 100 + 250


def test_parse_prometheus_handles_empty_counter() -> None:
    result = parse_prometheus(SAMPLE_METRICS)
    # audio_frames_dropped has no samples — should still be present as 0.
    assert result["gr_dis_audio_frames_dropped_total"] == 0.0


def test_parse_prometheus_extracts_histogram_buckets() -> None:
    result = parse_prometheus(SAMPLE_METRICS)
    hist = result["gr_dis_e2e_latency_seconds"]
    assert isinstance(hist, dict)
    assert hist["count"] == 1000.0
    buckets: dict[float, float] = hist["buckets"]  # type: ignore[assignment]
    assert buckets[0.25] == 990.0
    assert buckets[0.5] == 999.0


def _make_metrics(
    *,
    signal_pdus: float,
    tx_pdus: float,
    dropped: float,
    hwm_drops: float,
    bucket_025: float,
    bucket_05: float,
    count: float,
) -> str:
    return f"""\
# HELP gr_dis_signal_pdus_sent_total x
# TYPE gr_dis_signal_pdus_sent_total counter
gr_dis_signal_pdus_sent_total{{channel="c"}} {signal_pdus}
# HELP gr_dis_transmitter_pdus_sent_total x
# TYPE gr_dis_transmitter_pdus_sent_total counter
gr_dis_transmitter_pdus_sent_total{{radio="r"}} {tx_pdus}
# HELP gr_dis_audio_frames_dropped_total x
# TYPE gr_dis_audio_frames_dropped_total counter
gr_dis_audio_frames_dropped_total{{channel="c",reason="x"}} {dropped}
# HELP gr_dis_zmq_hwm_drops_total x
# TYPE gr_dis_zmq_hwm_drops_total counter
gr_dis_zmq_hwm_drops_total{{channel="c"}} {hwm_drops}
# HELP gr_dis_e2e_latency_seconds x
# TYPE gr_dis_e2e_latency_seconds histogram
gr_dis_e2e_latency_seconds_bucket{{le="0.25"}} {bucket_025}
gr_dis_e2e_latency_seconds_bucket{{le="0.5"}} {bucket_05}
gr_dis_e2e_latency_seconds_bucket{{le="+Inf"}} {count}
gr_dis_e2e_latency_seconds_count {count}
gr_dis_e2e_latency_seconds_sum 0
"""


def test_build_report_subtracts_counter_baselines() -> None:
    before = _make_metrics(
        signal_pdus=10, tx_pdus=2, dropped=0, hwm_drops=0,
        bucket_025=5, bucket_05=10, count=10,
    )
    after = _make_metrics(
        signal_pdus=1010, tx_pdus=12, dropped=0, hwm_drops=0,
        bucket_025=905, bucket_05=999, count=1010,
    )
    report = build_report(before, after, duration_s=60.0, n_channels=8)
    assert report.signal_pdus_total == 1000
    assert report.transmitter_pdus_total == 10
    assert report.duration_s == 60.0
    assert report.n_channels == 8
    assert 0 < report.median_le_250ms_fraction < 1
    assert 0 < report.p99_le_500ms_fraction <= 1


def test_build_report_passes_when_quantiles_clear_ac() -> None:
    before = _make_metrics(
        signal_pdus=0, tx_pdus=0, dropped=0, hwm_drops=0,
        bucket_025=0, bucket_05=0, count=0,
    )
    after = _make_metrics(
        signal_pdus=1000, tx_pdus=10, dropped=0, hwm_drops=0,
        bucket_025=600, bucket_05=995, count=1000,
    )
    report = build_report(after_text=after, before_text=before, duration_s=10.0, n_channels=8)
    assert report.median_le_250ms_fraction == 0.60
    assert report.p99_le_500ms_fraction == 0.995
    assert report.median_pass
    assert report.p99_pass
    assert report.drops_pass
    assert report.overall_pass


def test_build_report_fails_when_p99_misses() -> None:
    before = _make_metrics(
        signal_pdus=0, tx_pdus=0, dropped=0, hwm_drops=0,
        bucket_025=0, bucket_05=0, count=0,
    )
    after = _make_metrics(
        signal_pdus=1000, tx_pdus=10, dropped=0, hwm_drops=0,
        bucket_025=600, bucket_05=980, count=1000,  # only 98% under 500 ms
    )
    report = build_report(after_text=after, before_text=before, duration_s=10.0, n_channels=8)
    assert report.median_pass  # median is healthy; only p99 is the failure
    assert not report.p99_pass
    assert not report.overall_pass


def test_build_report_fails_on_hwm_drops() -> None:
    before = _make_metrics(
        signal_pdus=0, tx_pdus=0, dropped=0, hwm_drops=0,
        bucket_025=0, bucket_05=0, count=0,
    )
    after = _make_metrics(
        signal_pdus=1000, tx_pdus=10, dropped=0, hwm_drops=3,
        bucket_025=600, bucket_05=999, count=1000,
    )
    report = build_report(after_text=after, before_text=before, duration_s=10.0, n_channels=8)
    assert not report.drops_pass
    assert not report.overall_pass


def test_build_report_zero_count_is_failure() -> None:
    """No latency samples observed → can't claim pass."""
    before = _make_metrics(
        signal_pdus=0, tx_pdus=0, dropped=0, hwm_drops=0,
        bucket_025=0, bucket_05=0, count=0,
    )
    after = before
    report = build_report(after_text=after, before_text=before, duration_s=10.0, n_channels=8)
    assert report.signal_pdus_total == 0
    assert not report.overall_pass


def test_compute_idle_range_matches_duty_cycle_formula() -> None:
    # mean_idle = mean_talk * (1 - dc) / dc; talk range U[1, 8], mean 4.5
    lo, hi = compute_idle_range(0.25)
    assert math.isclose((lo + hi) / 2, 13.5, abs_tol=0.01)
    # Spread is 0.3 * mean .. 1.7 * mean
    assert math.isclose(lo, 13.5 * 0.3, abs_tol=0.01)
    assert math.isclose(hi, 13.5 * 1.7, abs_tol=0.01)


def test_compute_idle_range_half_duty() -> None:
    lo, hi = compute_idle_range(0.5)
    assert math.isclose((lo + hi) / 2, 4.5, abs_tol=0.01)


def test_compute_idle_range_rejects_invalid() -> None:
    import pytest
    with pytest.raises(ValueError):
        compute_idle_range(0.0)
    with pytest.raises(ValueError):
        compute_idle_range(1.0)
    with pytest.raises(ValueError):
        compute_idle_range(-0.1)


def test_iter_dwell_sequence_is_deterministic_per_seed() -> None:
    seq_a = list(iter_dwell_sequence(seed=42, duty_cycle=0.25, n=10))
    seq_b = list(iter_dwell_sequence(seed=42, duty_cycle=0.25, n=10))
    assert seq_a == seq_b


def test_iter_dwell_sequence_alternates_states_after_initial_idle() -> None:
    """First dwell is always IDLE (initial offset), then alternates TALK/IDLE/TALK/..."""
    seq = list(iter_dwell_sequence(seed=1, duty_cycle=0.25, n=6))
    states = [item[0] for item in seq]
    assert states[0] == FSMState.IDLE
    assert states[1] == FSMState.TALK
    assert states[2] == FSMState.IDLE
    assert states[3] == FSMState.TALK
    assert states[4] == FSMState.IDLE
    assert states[5] == FSMState.TALK


def test_iter_dwell_sequence_initial_idle_in_offset_range() -> None:
    """The very first IDLE dwell is uniform in [0, mean_idle] (initial offset)."""
    lo, hi = compute_idle_range(0.25)
    mean_idle = (lo + hi) / 2
    for seed in range(50):
        first = next(iter(iter_dwell_sequence(seed=seed, duty_cycle=0.25, n=1)))
        state, dwell = first
        assert state == FSMState.IDLE
        assert 0.0 <= dwell <= mean_idle + 0.01


def test_iter_dwell_sequence_talk_in_range() -> None:
    """TALK dwells are in [1.0, 8.0]."""
    for state, dwell in iter_dwell_sequence(seed=7, duty_cycle=0.25, n=50):
        if state == FSMState.TALK:
            assert 1.0 <= dwell <= 8.0


def test_iter_dwell_sequence_non_initial_idle_in_full_range() -> None:
    """Non-initial IDLE dwells are in compute_idle_range, not the half-width offset."""
    lo, hi = compute_idle_range(0.25)
    seen_non_initial_idle = False
    for i, (state, dwell) in enumerate(iter_dwell_sequence(seed=11, duty_cycle=0.25, n=50)):
        if i > 0 and state == FSMState.IDLE:
            assert lo <= dwell <= hi
            seen_non_initial_idle = True
    assert seen_non_initial_idle
