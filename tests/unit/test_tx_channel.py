"""Unit tests for TxChannelState."""

from __future__ import annotations

from gr_dis.bridge.tx_channel import TxChannelState

_KEY_A = (1, 100, 42, 1)
_KEY_B = (1, 100, 99, 2)


def _make_state(**kwargs: object) -> TxChannelState:
    defaults: dict = dict(
        channel_id="ch0",
        rf_freq_hz=144_800_000,
        bandwidth_hz=16_000,
        authorized=True,
        accepted_mod_keys={(2, 5)},
        tx_filter=None,
    )
    defaults.update(kwargs)
    return TxChannelState(**defaults)  # type: ignore[arg-type]


def test_try_acquire_when_free() -> None:
    state = _make_state()
    assert state.try_acquire(_KEY_A) is True
    assert state.active_holder == _KEY_A


def test_try_acquire_same_key_idempotent() -> None:
    state = _make_state()
    state.try_acquire(_KEY_A)
    assert state.try_acquire(_KEY_A) is True  # already held by A


def test_try_acquire_blocked_by_other() -> None:
    state = _make_state()
    state.try_acquire(_KEY_A)
    assert state.try_acquire(_KEY_B) is False
    assert state.active_holder == _KEY_A  # A still holds


def test_release_by_holder_clears_lock() -> None:
    state = _make_state()
    state.try_acquire(_KEY_A)
    state.release(_KEY_A)
    assert state.active_holder is None


def test_release_by_non_holder_is_noop() -> None:
    state = _make_state()
    state.try_acquire(_KEY_A)
    state.release(_KEY_B)  # B doesn't hold; should not clear
    assert state.active_holder == _KEY_A


def test_is_held_by() -> None:
    state = _make_state()
    assert state.is_held_by(_KEY_A) is False
    state.try_acquire(_KEY_A)
    assert state.is_held_by(_KEY_A) is True
    assert state.is_held_by(_KEY_B) is False


def test_matches_frequency_exact() -> None:
    state = _make_state(rf_freq_hz=144_800_000, bandwidth_hz=16_000)
    assert state.matches_frequency(144_800_000) is True


def test_matches_frequency_within_tolerance() -> None:
    state = _make_state(rf_freq_hz=144_800_000, bandwidth_hz=16_000)
    # ±8000 Hz tolerance (bandwidth_hz / 2)
    assert state.matches_frequency(144_808_000) is True
    assert state.matches_frequency(144_792_000) is True


def test_matches_frequency_outside_tolerance() -> None:
    state = _make_state(rf_freq_hz=144_800_000, bandwidth_hz=16_000)
    assert state.matches_frequency(144_820_000) is False
    assert state.matches_frequency(144_780_000) is False


def test_second_acquire_after_release() -> None:
    state = _make_state()
    state.try_acquire(_KEY_A)
    state.release(_KEY_A)
    assert state.try_acquire(_KEY_B) is True
    assert state.active_holder == _KEY_B
