"""Per-channel TX state: frequency matching and first-wins transmit lock."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from gr_dis.engine.config import TxFilterConfig

_EntityKey = tuple[int, int, int, int]  # (site, app, entity, radio_id)


@dataclass
class TxChannelState:
    channel_id: str
    rf_freq_hz: int
    bandwidth_hz: int
    authorized: bool
    accepted_mod_keys: set[tuple[int, int]]
    tx_filter: TxFilterConfig | None
    active_holder: _EntityKey | None = field(default=None)

    def matches_frequency(self, rf_freq_hz: int) -> bool:
        """True if rf_freq_hz is within ±bandwidth_hz/2 of this channel's centre."""
        return abs(rf_freq_hz - self.rf_freq_hz) <= self.bandwidth_hz // 2

    def try_acquire(self, key: _EntityKey) -> bool:
        """Acquire the TX lock; returns True if acquired or already held by key."""
        if self.active_holder is None:
            self.active_holder = key
            return True
        return self.active_holder == key

    def release(self, key: _EntityKey) -> None:
        """Release the TX lock if currently held by key."""
        if self.active_holder == key:
            self.active_holder = None

    def is_held_by(self, key: _EntityKey) -> bool:
        return self.active_holder == key
