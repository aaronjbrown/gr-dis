"""asyncio UDP listener — routes incoming DIS PDUs to the TX path."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from gr_dis.bridge.encoder_ulaw import ulaw2lin
from gr_dis.bridge.multicast import make_listener_socket
from gr_dis.bridge.pdu.emission import derive_emission_designator
from gr_dis.bridge.pdu.enums import (
    ENCODING_SCHEME_ULAW_8K,
    PDU_TYPE_SIGNAL,
    PDU_TYPE_TRANSMITTER,
    TRANSMIT_STATE_ON_TX,
)
from gr_dis.bridge.pdu.parser import (
    ParseError,
    parse_header,
    parse_signal_pdu,
    parse_transmitter_pdu,
)

if TYPE_CHECKING:
    from gr_dis.bridge.tx_channel import TxChannelState
    from gr_dis.bridge.tx_publisher import TxPublisher
    from gr_dis.metrics import BridgeMetrics

logger = logging.getLogger(__name__)


def _handle_transmitter(
    data: bytes,
    channels: dict[str, TxChannelState],
    metrics: BridgeMetrics,
) -> None:
    try:
        tx = parse_transmitter_pdu(data)
    except ParseError as exc:
        metrics.rx_pdu_parse_errors.inc()
        logger.debug("Transmitter PDU parse error: %s", exc)
        return

    channel = None
    for ch in channels.values():
        if ch.matches_frequency(tx.rf_freq_hz):
            channel = ch
            break
    if channel is None:
        return

    metrics.rx_transmitter_pdus_received.labels(channel=channel.channel_id).inc()

    if channel.tx_filter is not None:
        f = channel.tx_filter
        if (tx.entity_site, tx.entity_app, tx.entity_entity) != (
            f.entity_id.site, f.entity_id.app, f.entity_id.entity
        ):
            return
        if tx.radio_id != f.radio_id:
            return

    mod_key = (tx.mod_major, tx.mod_detail)
    if mod_key not in channel.accepted_mod_keys:
        designator = (
            derive_emission_designator(tx.mod_major, tx.mod_detail, tx.bandwidth_hz)
            or "unknown"
        )
        logger.debug(
            "modulation mismatch on %s: received %s, accepted %s",
            channel.channel_id, designator, channel.accepted_mod_keys,
        )
        metrics.tx_audio_frames_dropped.labels(
            channel=channel.channel_id, reason="modulation_mismatch"
        ).inc()
        return

    key = (tx.entity_site, tx.entity_app, tx.entity_entity, tx.radio_id)
    if tx.transmit_state == TRANSMIT_STATE_ON_TX:
        channel.try_acquire(key)
    else:
        channel.release(key)


def _handle_signal(
    data: bytes,
    channels: dict[str, TxChannelState],
    publisher: TxPublisher,
    metrics: BridgeMetrics,
) -> None:
    try:
        sig = parse_signal_pdu(data)
    except ParseError as exc:
        metrics.rx_pdu_parse_errors.inc()
        logger.debug("Signal PDU parse error: %s", exc)
        return

    key = (sig.entity_site, sig.entity_app, sig.entity_entity, sig.radio_id)

    channel = None
    for ch in channels.values():
        if ch.is_held_by(key):
            channel = ch
            break
    if channel is None:
        return

    metrics.rx_signal_pdus_received.labels(channel=channel.channel_id).inc()

    if not channel.authorized:
        metrics.tx_audio_frames_dropped.labels(
            channel=channel.channel_id, reason="unauthorized"
        ).inc()
        return

    if sig.encoding_scheme != ENCODING_SCHEME_ULAW_8K:
        metrics.tx_audio_frames_dropped.labels(
            channel=channel.channel_id, reason="encoding_unsupported"
        ).inc()
        return

    pcm = ulaw2lin(sig.audio_data)
    publisher.publish(channel.channel_id, pcm)
    metrics.tx_audio_frames_published.labels(channel=channel.channel_id).inc()


async def run_dis_listener(
    multicast_ip: str,
    port: int,
    exercise_id: int,
    channels: dict[str, TxChannelState],
    publisher: TxPublisher,
    metrics: BridgeMetrics,
) -> None:
    """Listen for DIS PDUs on multicast and route audio to tx_publisher.

    Runs until cancelled. Uses add_reader for non-blocking I/O.
    """
    sock = make_listener_socket(multicast_ip, port)
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=2000)

    def _reader() -> None:
        while True:
            try:
                data, _ = sock.recvfrom(65535)
                try:
                    queue.put_nowait(data)
                except asyncio.QueueFull:
                    metrics.rx_pdu_queue_drops.inc()
                    break
            except BlockingIOError:
                break

    loop.add_reader(sock.fileno(), _reader)
    logger.info(
        "DIS listener on %s:%d (exercise_id=%d, %d TX channel(s))",
        multicast_ip, port, exercise_id, len(channels),
    )
    try:
        while True:
            data = await queue.get()
            try:
                hdr = parse_header(data)
            except ParseError:
                metrics.rx_pdu_parse_errors.inc()
                continue
            if hdr.exercise_id != exercise_id:
                continue
            if hdr.pdu_type == PDU_TYPE_TRANSMITTER:
                _handle_transmitter(data, channels, metrics)
            elif hdr.pdu_type == PDU_TYPE_SIGNAL:
                _handle_signal(data, channels, publisher, metrics)
    except asyncio.CancelledError:
        raise
    finally:
        loop.remove_reader(sock.fileno())
        sock.close()
        logger.info("DIS listener closed")
