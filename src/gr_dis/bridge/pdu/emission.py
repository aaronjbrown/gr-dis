"""DIS modulation parameters → ITU emission designator (ITU-R SM.1138)."""

from __future__ import annotations

from gr_dis.bridge.pdu.enums import (
    MOD_DETAIL_FM_ANALOG,
    MOD_DETAIL_FM_ANGLE,
    MOD_MAJOR_AMPLITUDE_AND_ANGLE,
    MOD_MAJOR_ANGLE,
)

# (mod_major, mod_detail) → ITU emission type suffix
DIS_TO_EMISSION_SUFFIX: dict[tuple[int, int], str] = {
    (MOD_MAJOR_AMPLITUDE_AND_ANGLE, MOD_DETAIL_FM_ANALOG): "F3E",
    (MOD_MAJOR_ANGLE, MOD_DETAIL_FM_ANGLE): "F3E",
}


def format_itu_bandwidth(bandwidth_hz: float) -> str:
    """Convert Hz to ITU 4-character bandwidth notation.

    The multiplier letter (H, K, M, G) replaces the decimal point.
    Examples: 16000 → "16K0", 25000 → "25K0", 200000 → "200K", 8500 → "8K50"
    """
    if bandwidth_hz >= 1_000_000_000:
        scaled, mult = bandwidth_hz / 1_000_000_000, "G"
    elif bandwidth_hz >= 1_000_000:
        scaled, mult = bandwidth_hz / 1_000_000, "M"
    elif bandwidth_hz >= 1_000:
        scaled, mult = bandwidth_hz / 1_000, "K"
    else:
        scaled, mult = bandwidth_hz, "H"

    if scaled >= 100:
        return f"{int(round(scaled))}{mult}"
    elif scaled >= 10:
        int_part = int(scaled)
        frac = round((scaled - int_part) * 10)
        if frac == 10:
            int_part += 1
            frac = 0
        return f"{int_part}{mult}{frac}"
    else:
        int_part = int(scaled)
        frac = round((scaled - int_part) * 100)
        if frac == 100:
            int_part += 1
            frac = 0
        return f"{int_part}{mult}{frac:02d}"


def derive_emission_designator(
    mod_major: int,
    mod_detail: int,
    bandwidth_hz: float,
) -> str | None:
    """Return ITU emission designator, e.g. "16K0F3E", or None if unknown."""
    suffix = DIS_TO_EMISSION_SUFFIX.get((mod_major, mod_detail))
    if suffix is None:
        return None
    return f"{format_itu_bandwidth(bandwidth_hz)}{suffix}"
