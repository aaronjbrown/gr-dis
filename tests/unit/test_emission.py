"""Unit tests for ITU emission designator derivation."""

from __future__ import annotations

import pytest

from gr_dis.bridge.pdu.emission import derive_emission_designator, format_itu_bandwidth
from gr_dis.bridge.pdu.enums import (
    MOD_DETAIL_FM_ANALOG,
    MOD_DETAIL_FM_ANGLE,
    MOD_MAJOR_AMPLITUDE_AND_ANGLE,
    MOD_MAJOR_ANGLE,
)


@pytest.mark.parametrize("hz,expected", [
    (16_000, "16K0"),
    (11_000, "11K0"),
    (25_000, "25K0"),
    (200_000, "200K"),
    (12_500, "12K5"),
    (8_500, "8K50"),
    (2_700, "2K70"),
    (1_000_000, "1M00"),
    (100, "100H"),
])
def test_format_itu_bandwidth(hz: float, expected: str) -> None:
    assert format_itu_bandwidth(hz) == expected


def test_nbfm_designator() -> None:
    result = derive_emission_designator(MOD_MAJOR_AMPLITUDE_AND_ANGLE, MOD_DETAIL_FM_ANALOG, 16_000)
    assert result == "16K0F3E"


def test_wfm_designator() -> None:
    result = derive_emission_designator(MOD_MAJOR_ANGLE, MOD_DETAIL_FM_ANGLE, 200_000)
    assert result == "200KF3E"


def test_unknown_modulation_returns_none() -> None:
    result = derive_emission_designator(99, 99, 16_000)
    assert result is None


def test_nbfm_25k_designator() -> None:
    result = derive_emission_designator(MOD_MAJOR_AMPLITUDE_AND_ANGLE, MOD_DETAIL_FM_ANALOG, 25_000)
    assert result == "25K0F3E"
