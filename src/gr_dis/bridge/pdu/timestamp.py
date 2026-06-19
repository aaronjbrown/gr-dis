"""DIS absolute timestamp (IEEE 1278.1-2012 §6.2.88)."""

from __future__ import annotations

import time

_TICKS_PER_HOUR = 2**31 - 1


def dis_timestamp(seconds_past_hour: float | None = None) -> int:
    """Return a 32-bit DIS absolute timestamp.

    Low bit = 1 signals an absolute (wall-clock-locked) timestamp.
    ``seconds_past_hour`` defaults to the current CLOCK_REALTIME value mod 3600.
    Pass a fixed value in tests for deterministic output.
    """
    if seconds_past_hour is None:
        seconds_past_hour = time.time() % 3600.0
    ticks = int(seconds_past_hour * _TICKS_PER_HOUR / 3600.0)
    return (ticks << 1) | 1
