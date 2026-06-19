"""Prometheus parsing + StressReport for multi-channel stress harness.

Pure data — no I/O coupling. Used by both the operator CLI script and the
pytest smoke test.
"""

from __future__ import annotations

import urllib.request
from dataclasses import dataclass

from prometheus_client.parser import text_string_to_metric_families


def parse_prometheus(text: str) -> dict[str, float | dict[str, object]]:
    """Parse Prometheus text-format `/metrics` output.

    Counters and gauges return summed-across-labels float totals.
    Histograms return `{"buckets": {le_float: cum_count}, "count": N, "sum": S}`.
    Metrics with zero samples still appear with value 0.0 (counters) or empty
    bucket dict (histograms).
    """
    result: dict[str, float | dict[str, object]] = {}
    for family in text_string_to_metric_families(text):
        if family.type == "histogram":
            buckets: dict[float, float] = {}
            count = 0.0
            total_sum = 0.0
            for sample in family.samples:
                if sample.name.endswith("_bucket"):
                    le = sample.labels.get("le")
                    if le is None:
                        continue
                    le_val = float("inf") if le == "+Inf" else float(le)
                    buckets[le_val] = sample.value
                elif sample.name.endswith("_count"):
                    count = sample.value
                elif sample.name.endswith("_sum"):
                    total_sum = sample.value
            result[family.name] = {
                "buckets": buckets,
                "count": count,
                "sum": total_sum,
            }
        else:
            total = 0.0
            for sample in family.samples:
                # Skip the `_created` timestamp samples that counter families
                # emit alongside the value.
                if sample.name.endswith("_created"):
                    continue
                total += sample.value
            # The prometheus_client parser strips the `_total` suffix from
            # counter family names (OpenMetrics convention).  Re-append it so
            # callers can use the same name they see in the raw text output.
            key = (
                family.name + "_total"
                if family.type == "counter"
                else family.name
            )
            result[key] = total
    return result


@dataclass(frozen=True)
class StressReport:
    """Pass/fail outcome of a stress run, computed from `/metrics` deltas.

    The two `*_fraction` fields are **cumulative bucket fractions**, not
    latency values. `median_le_250ms_fraction` is the fraction of samples
    whose end-to-end latency was <= 250 ms (the NFR-1 median ceiling).
    `p99_le_500ms_fraction` is the fraction <= 500 ms (the NFR-1 p99 ceiling).
    The corresponding `median_pass` and `p99_pass` properties check those
    fractions against the AC thresholds (>= 0.50 and >= 0.99 respectively).
    """

    duration_s: float
    n_channels: int
    signal_pdus_total: int
    transmitter_pdus_total: int
    frames_dropped_total: int
    zmq_hwm_drops_total: int
    latency_count: int
    median_le_250ms_fraction: float
    p99_le_500ms_fraction: float

    @property
    def median_pass(self) -> bool:
        return self.median_le_250ms_fraction >= 0.50

    @property
    def p99_pass(self) -> bool:
        return self.p99_le_500ms_fraction >= 0.99

    @property
    def drops_pass(self) -> bool:
        return self.frames_dropped_total == 0 and self.zmq_hwm_drops_total == 0

    @property
    def has_samples(self) -> bool:
        return self.latency_count > 0

    @property
    def overall_pass(self) -> bool:
        return (
            self.has_samples
            and self.median_pass
            and self.p99_pass
            and self.drops_pass
        )


def build_report(
    before_text: str,
    after_text: str,
    *,
    duration_s: float,
    n_channels: int,
) -> StressReport:
    """Compute pass/fail report from before/after `/metrics` snapshots."""
    before = parse_prometheus(before_text)
    after = parse_prometheus(after_text)

    def delta_counter(name: str) -> float:
        b = before.get(name, 0.0)
        a = after.get(name, 0.0)
        # Counters: stored as float at this layer. Histograms would be dicts —
        # they're not passed here.
        assert isinstance(a, (int, float)) and isinstance(b, (int, float))
        # Counters can't logically decrease; clamp guards against metric-family
        # changes between snapshots.
        return max(0.0, float(a) - float(b))

    def delta_histogram_count(name: str) -> float:
        b_h = before.get(name)
        a_h = after.get(name)
        b_count = float(b_h.get("count", 0.0)) if isinstance(b_h, dict) else 0.0  # type: ignore[arg-type]
        a_count = float(a_h.get("count", 0.0)) if isinstance(a_h, dict) else 0.0  # type: ignore[arg-type]
        return max(0.0, a_count - b_count)

    def delta_histogram_bucket(name: str, le: float) -> float:
        b_h = before.get(name)
        a_h = after.get(name)
        if isinstance(b_h, dict):
            b_buckets: dict[float, float] = b_h["buckets"]  # type: ignore[assignment]
            b_bucket = float(b_buckets.get(le, 0.0))
        else:
            b_bucket = 0.0
        if isinstance(a_h, dict):
            a_buckets: dict[float, float] = a_h["buckets"]  # type: ignore[assignment]
            a_bucket = float(a_buckets.get(le, 0.0))
        else:
            a_bucket = 0.0
        return max(0.0, a_bucket - b_bucket)

    latency_count = delta_histogram_count("gr_dis_e2e_latency_seconds")
    if latency_count > 0:
        median_frac = (
            delta_histogram_bucket("gr_dis_e2e_latency_seconds", 0.25)
            / latency_count
        )
        p99_frac = (
            delta_histogram_bucket("gr_dis_e2e_latency_seconds", 0.5)
            / latency_count
        )
    else:
        median_frac = 0.0
        p99_frac = 0.0

    return StressReport(
        duration_s=duration_s,
        n_channels=n_channels,
        signal_pdus_total=int(delta_counter("gr_dis_signal_pdus_sent_total")),
        transmitter_pdus_total=int(delta_counter("gr_dis_transmitter_pdus_sent_total")),
        frames_dropped_total=int(delta_counter("gr_dis_audio_frames_dropped_total")),
        zmq_hwm_drops_total=int(delta_counter("gr_dis_zmq_hwm_drops_total")),
        latency_count=int(latency_count),
        median_le_250ms_fraction=median_frac,
        p99_le_500ms_fraction=p99_frac,
    )


def scrape(metrics_url: str, timeout_s: float = 3.0) -> str:
    """GET the bridge's `/metrics` endpoint, return text body."""
    with urllib.request.urlopen(metrics_url, timeout=timeout_s) as resp:
        body: str = resp.read().decode("utf-8")
    return body


def format_report(report: StressReport) -> str:
    """Multi-line human-readable final report block."""
    pdus_per_s = (
        report.signal_pdus_total / report.duration_s
        if report.duration_s > 0
        else 0.0
    )

    def _verdict(ok: bool) -> str:
        return "PASS" if ok else "FAIL"

    lines = [
        "=== stress report =================================================",
        f"Duration:         {report.duration_s:.1f} s",
        f"Channels:         {report.n_channels}",
        f"Signal PDUs:      {report.signal_pdus_total}  ({pdus_per_s:.1f}/s)",
        f"Transmitter PDUs: {report.transmitter_pdus_total}",
        f"Frames dropped:   {report.frames_dropped_total}",
        f"ZMQ HWM drops:    {report.zmq_hwm_drops_total}",
        f"Latency samples:  {report.latency_count}",
        f"Median latency:   <= 0.25 s ({report.median_le_250ms_fraction * 100:.3f}% of samples)  "
        f"{_verdict(report.median_pass)}",
        f"p99 latency:      <= 0.50 s ({report.p99_le_500ms_fraction * 100:.3f}% of samples)  "
        f"{_verdict(report.p99_pass)}",
        f"Result:           {_verdict(report.overall_pass)}",
    ]
    return "\n".join(lines)
