#!/usr/bin/env python3
"""Probe the bridge ZMQ TX PUB socket — prints received frame count & RMS.

Usage:
    .venv/bin/python scripts/zmq_tx_probe.py [duration_s]

Run while the bridge and synthetic_tx.py are active.  Exits after
duration_s (default 15) and prints a summary.
"""

from __future__ import annotations

import struct
import sys
import time

import zmq

ENDPOINT = "tcp://127.0.0.1:5556"
CHANNEL_ID = "vhf_ch1"
TOPIC = f"tx_audio.{CHANNEL_ID}".encode("ascii")


def main(duration_s: float = 15.0) -> None:
    ctx = zmq.Context()
    sock = ctx.socket(zmq.SUB)
    sock.setsockopt(zmq.RCVHWM, 500)
    sock.setsockopt(zmq.SUBSCRIBE, TOPIC)
    sock.connect(ENDPOINT)
    print(f"SUB connected → {ENDPOINT}  topic={TOPIC.decode()}")

    deadline = time.monotonic() + duration_s
    frame_count = 0
    total_samples = 0
    sum_sq = 0.0

    while time.monotonic() < deadline:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        if sock.poll(timeout=int(remaining * 1000)):
            parts = sock.recv_multipart(flags=zmq.NOBLOCK)
            if len(parts) == 3:
                payload = parts[2]
                n = len(payload) // 2
                samples = struct.unpack(f"<{n}h", payload[:n * 2])
                frame_count += 1
                total_samples += n
                sum_sq += sum(s * s for s in samples)
                if frame_count % 50 == 0:
                    rms = (sum_sq / total_samples) ** 0.5 if total_samples else 0
                    print(f"  {frame_count} frames  rms={rms:.1f}", end="\r", flush=True)

    rms = (sum_sq / total_samples) ** 0.5 if total_samples else 0
    print(f"\n--- {frame_count} frames received, {total_samples} samples, RMS={rms:.1f}")
    sock.close()
    ctx.term()


if __name__ == "__main__":
    duration = float(sys.argv[1]) if len(sys.argv) > 1 else 15.0
    main(duration)
