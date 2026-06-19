"""Async ZMQ SUB consumer — dispatches messages to per-channel handlers."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import msgpack
import zmq
import zmq.asyncio

if TYPE_CHECKING:
    from gr_dis.bridge.radio_state import RadioChannelHandler

logger = logging.getLogger(__name__)


async def run_subscriber(
    zmq_bind: str,
    handlers: dict[str, RadioChannelHandler],
) -> None:
    """Bind a ZMQ SUB socket and dispatch messages until cancelled.

    Each message is a 3-frame ZMQ multipart message:
      [0] topic (UTF-8 ASCII)
      [1] header (MessagePack object)
      [2] payload (bytes, may be empty)

    Topics: ``audio.<channel_id>``, ``meta.<channel_id>``, ``event.<channel_id>``
    """
    ctx = zmq.asyncio.Context.instance()
    sub: zmq.asyncio.Socket = ctx.socket(zmq.SUB)
    sub.setsockopt(zmq.RCVHWM, 1000)
    sub.bind(zmq_bind)
    for prefix in (b"audio.", b"meta.", b"event."):
        sub.setsockopt(zmq.SUBSCRIBE, prefix)

    logger.info("ZMQ SUB bound to %s", zmq_bind)
    try:
        while True:
            frames = await sub.recv_multipart()
            if len(frames) < 3:
                logger.warning("malformed ZMQ message: %d frames", len(frames))
                continue

            topic = frames[0].decode("ascii", errors="replace")
            try:
                header: dict[str, Any] = msgpack.unpackb(frames[1], raw=False)
            except Exception as exc:
                logger.warning("msgpack decode error on topic %r: %s", topic, exc)
                continue

            # Version check
            if header.get("v", 1) > 1:
                logger.warning("unknown wire protocol version %s on %r", header.get("v"), topic)
                continue

            payload = frames[2]
            kind, _, channel_id = topic.partition(".")
            handler = handlers.get(channel_id)
            if handler is None:
                continue  # unknown channel; ignore

            try:
                if kind == "audio":
                    handler.handle_audio(header, payload)
                elif kind == "event":
                    handler.handle_event(header, payload)
                elif kind == "meta":
                    handler.handle_meta(header, payload)
            except Exception as exc:
                logger.exception("handler error on %r: %s", topic, exc)

    except Exception as exc:
        logger.error("subscriber loop exited unexpectedly: %s", exc)
        raise
    finally:
        sub.close(linger=0)
        logger.info("ZMQ SUB closed")
