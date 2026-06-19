"""Per-radio channel handler: FSM + PDU emission + heartbeat."""

from __future__ import annotations

import asyncio
import dataclasses
import logging
import time
from typing import TYPE_CHECKING, Any

from gr_dis.bridge.encoder_ulaw import lin2ulaw
from gr_dis.bridge.pdu.enums import (
    CHAIN_MODULATION,
    MOD_DETAIL_FM_ANGLE,
    MOD_MAJOR_ANGLE,
    MOD_SPREAD_SPECTRUM,
    MOD_SYSTEM_GENERIC,
    TRANSMIT_STATE_OFF,
    TRANSMIT_STATE_ON_NOT_TX,
    TRANSMIT_STATE_ON_TX,
)
from gr_dis.bridge.pdu.signal import SignalState, build_signal_pdu
from gr_dis.bridge.pdu.transmitter import TransmitterState, build_transmitter_pdu
from gr_dis.engine.config import IdleBehavior

if TYPE_CHECKING:
    import socket

    from gr_dis.engine.config import ChannelConfig, DISConfig
    from gr_dis.metrics import BridgeMetrics

logger = logging.getLogger(__name__)


class RadioChannelHandler:
    """Handles ZMQ messages for one channel and emits DIS PDUs.

    State machine:
        On-not-tx ──squelch_open──▶ On-transmitting
        On-transmitting ──squelch_close──▶ On-not-tx

    The handler starts in On-not-tx; Off state is not emitted during normal operation.
    """

    def __init__(
        self,
        channel_cfg: ChannelConfig,
        dis_config: DISConfig,
        sock: socket.socket,
        metrics: BridgeMetrics,
    ) -> None:
        self.channel_id = channel_cfg.id
        self._radio = channel_cfg.radio
        self._dis = dis_config
        self._sock = sock
        self._metrics = metrics
        self._dest = (dis_config.multicast, dis_config.port)
        self._last_seq: int | None = None

        r = channel_cfg.radio
        d = dis_config
        ecef = tuple(r.antenna_location_ecef)
        rel = tuple(r.relative_antenna_location)
        assert len(ecef) == 3 and len(rel) == 3

        _default_mod = (
            MOD_SPREAD_SPECTRUM, MOD_MAJOR_ANGLE,
            MOD_DETAIL_FM_ANGLE, MOD_SYSTEM_GENERIC,
        )
        mod_spread, mod_major, mod_detail, mod_system = (
            CHAIN_MODULATION.get(channel_cfg.chain, _default_mod)
        )

        self._tx_state = TransmitterState(
            exercise_id=d.exercise_id,
            entity_site=r.entity_id.site,
            entity_app=r.entity_id.app,
            entity_entity=r.entity_id.entity,
            radio_id=r.radio_id,
            kind=r.radio_entity_type.kind,
            domain=r.radio_entity_type.domain,
            country=r.radio_entity_type.country,
            category=r.radio_entity_type.category,
            subcategory=r.radio_entity_type.subcategory,
            specific=r.radio_entity_type.specific,
            extra=r.radio_entity_type.extra,
            transmit_state=TRANSMIT_STATE_ON_NOT_TX,
            input_source=r.input_source,
            antenna_location_ecef=(ecef[0], ecef[1], ecef[2]),
            relative_antenna_location=(rel[0], rel[1], rel[2]),
            rf_freq_hz=channel_cfg.rf_freq_hz,
            bandwidth_hz=float(channel_cfg.bandwidth_hz),
            power_dbm=r.power_dbm,
            attached=r.attached,
            mod_spread=mod_spread,
            mod_major=mod_major,
            mod_detail=mod_detail,
            mod_system=mod_system,
        )
        self._sig_state = SignalState(
            exercise_id=d.exercise_id,
            entity_site=r.entity_id.site,
            entity_app=r.entity_id.app,
            entity_entity=r.entity_id.entity,
            radio_id=r.radio_id,
            attached=r.attached,
        )

    # ------------------------------------------------------------------
    # PDU senders
    # ------------------------------------------------------------------

    def send_transmitter_pdu(self) -> None:
        pdu = build_transmitter_pdu(self._tx_state)
        try:
            self._sock.sendto(pdu, self._dest)
            self._metrics.transmitter_pdus_sent.labels(radio=self.channel_id).inc()
        except OSError as exc:
            logger.error("multicast send error (Transmitter, %s): %s", self.channel_id, exc)

    def send_signal_pdu(self, ulaw_bytes: bytes) -> None:
        pdu = build_signal_pdu(self._sig_state, ulaw_bytes)
        try:
            self._sock.sendto(pdu, self._dest)
            self._metrics.signal_pdus_sent.labels(channel=self.channel_id).inc()
        except OSError as exc:
            logger.error("multicast send error (Signal, %s): %s", self.channel_id, exc)

    def send_off_transmitter_pdu(self) -> None:
        off_state = dataclasses.replace(self._tx_state, transmit_state=TRANSMIT_STATE_OFF)
        pdu = build_transmitter_pdu(off_state)
        try:
            self._sock.sendto(pdu, self._dest)
            self._metrics.transmitter_pdus_sent.labels(radio=self.channel_id).inc()
        except OSError as exc:
            logger.error("multicast send error (Transmitter Off, %s): %s", self.channel_id, exc)

    # ------------------------------------------------------------------
    # FSM helpers
    # ------------------------------------------------------------------

    def _set_state(self, new_state: int) -> bool:
        """Update transmit state; return True if it actually changed."""
        if self._tx_state.transmit_state != new_state:
            self._tx_state.transmit_state = new_state
            return True
        return False

    def _do_squelch_open(self) -> None:
        if self._set_state(TRANSMIT_STATE_ON_TX):
            self.send_transmitter_pdu()

    def _do_squelch_close(self) -> None:
        if self._set_state(TRANSMIT_STATE_ON_NOT_TX):
            self.send_transmitter_pdu()

    # ------------------------------------------------------------------
    # Message handlers
    # ------------------------------------------------------------------

    def handle_audio(self, header: dict[str, Any], payload: bytes) -> None:
        self._metrics.audio_frames_received.labels(channel=self.channel_id).inc()

        # Detect ZMQ HWM drops via sequence gap
        seq = header.get("seq")
        if seq is not None and self._last_seq is not None:
            gap = int(seq) - self._last_seq - 1
            if gap > 0:
                self._metrics.zmq_hwm_drops.labels(channel=self.channel_id).inc(gap)
                logger.warning("ZMQ seq gap %d on %s", gap, self.channel_id)
        self._last_seq = seq

        # E2E latency measurement
        host_ts_ns = header.get("host_ts_ns")
        if host_ts_ns is not None:
            latency_s = (time.time_ns() - int(host_ts_ns)) / 1e9
            if 0.0 < latency_s < 30.0:  # guard against clock skew
                self._metrics.e2e_latency.observe(latency_s)

        squelch: bool = bool(header.get("squelch", False))

        if squelch:
            self._do_squelch_open()
            if self._tx_state.transmit_state == TRANSMIT_STATE_ON_TX and payload:
                ulaw = lin2ulaw(payload)
                self.send_signal_pdu(ulaw)
        else:
            if self._dis.idle_behavior == IdleBehavior.hard_mute:
                self._do_squelch_close()
            elif payload:
                # send_silence: always emit Signal PDUs so the receiver's audio
                # queue never starves (even before the first squelch-open).
                # Transmitter PDU state still reflects real transmit state.
                n = len(payload) // 2
                silence = bytes([0x7F] * n)
                self.send_signal_pdu(silence)

    def handle_event(self, header: dict[str, Any], _payload: bytes) -> None:
        name: str = str(header.get("name", ""))
        if name == "squelch_open":
            self._do_squelch_open()
        elif name == "squelch_close":
            self._do_squelch_close()
        elif name == "freq_changed":
            data = header.get("data") or {}
            new_freq = data.get("rf_freq_hz")
            if new_freq is not None and int(new_freq) != self._tx_state.rf_freq_hz:
                self._tx_state.rf_freq_hz = int(new_freq)
                self.send_transmitter_pdu()
        elif name == "chain_error":
            logger.warning("chain_error on %s: %s", self.channel_id, header.get("data"))

    def handle_meta(self, header: dict[str, Any], _payload: bytes) -> None:
        new_freq = header.get("rf_freq_hz")
        if new_freq is not None and int(new_freq) != self._tx_state.rf_freq_hz:
            self._tx_state.rf_freq_hz = int(new_freq)
            self.send_transmitter_pdu()

    # ------------------------------------------------------------------
    # Heartbeat coroutine
    # ------------------------------------------------------------------

    async def heartbeat_loop(self) -> None:
        """Emit Transmitter PDUs at heartbeat_interval_seconds, staggered by radio_id."""
        interval = self._dis.heartbeat_interval_seconds
        initial_delay = float(self._radio.radio_id % max(1, int(interval)))
        await asyncio.sleep(initial_delay)
        while True:
            self.send_transmitter_pdu()
            await asyncio.sleep(interval)
