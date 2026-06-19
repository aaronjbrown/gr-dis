#!/usr/bin/env python3
"""Synthetic DIS transmitter for TX-loopback testing.

Sends Transmitter PDU (ON_TX) + Signal PDUs carrying a 400 Hz test tone to
the DIS multicast group.  Run while the bridge and tx_file_loopback flowgraph
are active to produce a valid NBFM IQ recording.

Usage:
    .venv/bin/python scripts/synthetic_tx.py           # 10 s tone
    .venv/bin/python scripts/synthetic_tx.py 30        # 30 s tone
"""

from __future__ import annotations

import math
import socket
import struct
import sys
import time

sys.path.insert(0, "src")

from gr_dis.bridge.encoder_ulaw import lin2ulaw
from gr_dis.bridge.pdu.enums import (
    MOD_DETAIL_FM_ANGLE,
    MOD_MAJOR_ANGLE,
    MOD_SPREAD_SPECTRUM,
    MOD_SYSTEM_GENERIC,
    TRANSMIT_STATE_ON_NOT_TX,
    TRANSMIT_STATE_ON_TX,
)
from gr_dis.bridge.pdu.signal import SignalState, build_signal_pdu
from gr_dis.bridge.pdu.transmitter import TransmitterState, build_transmitter_pdu

MULTICAST = "239.1.2.3"
PORT = 3000
EXERCISE_ID = 1
ENTITY_SITE = 1
ENTITY_APP = 200
ENTITY_ENTITY = 42
RADIO_ID = 5
RF_FREQ_HZ = 146_950_000
BANDWIDTH_HZ = 25_000.0

SAMPLE_RATE = 8_000
FRAME_SAMPLES = 160    # 20 ms @ 8 kHz
TONE_HZ = 400
AMPLITUDE = 16_384     # ~half int16 max — enough to clear the squelch threshold


def _make_sock() -> socket.socket:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 1)
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_LOOP, 1)
    return sock


def _tx_pdu(transmit_state: int) -> bytes:
    state = TransmitterState(
        exercise_id=EXERCISE_ID,
        entity_site=ENTITY_SITE,
        entity_app=ENTITY_APP,
        entity_entity=ENTITY_ENTITY,
        radio_id=RADIO_ID,
        kind=7, domain=3, country=225,
        category=1, subcategory=0, specific=0, extra=0,
        transmit_state=transmit_state,
        rf_freq_hz=RF_FREQ_HZ,
        bandwidth_hz=BANDWIDTH_HZ,
        power_dbm=10.0,
        mod_spread=MOD_SPREAD_SPECTRUM,
        mod_major=MOD_MAJOR_ANGLE,
        mod_detail=MOD_DETAIL_FM_ANGLE,
        mod_system=MOD_SYSTEM_GENERIC,
    )
    return build_transmitter_pdu(state)


def _sig_pdu(frame_idx: int) -> bytes:
    samples = [
        int(AMPLITUDE * math.sin(2 * math.pi * TONE_HZ * (frame_idx * FRAME_SAMPLES + i) / SAMPLE_RATE))  # noqa: E501
        for i in range(FRAME_SAMPLES)
    ]
    pcm = struct.pack(f"<{FRAME_SAMPLES}h", *samples)
    ulaw = lin2ulaw(pcm)
    state = SignalState(
        exercise_id=EXERCISE_ID,
        entity_site=ENTITY_SITE,
        entity_app=ENTITY_APP,
        entity_entity=ENTITY_ENTITY,
        radio_id=RADIO_ID,
        attached=False,
    )
    return build_signal_pdu(state, ulaw)


def main(duration_s: float = 10.0) -> None:
    sock = _make_sock()
    addr = (MULTICAST, PORT)

    sock.sendto(_tx_pdu(TRANSMIT_STATE_ON_TX), addr)
    print(f"ON_TX → {MULTICAST}:{PORT}  {RF_FREQ_HZ/1e6:.3f} MHz NBFM  ({duration_s:.0f} s)")

    frame_idx = 0
    frame_interval = FRAME_SAMPLES / SAMPLE_RATE  # 0.02 s
    next_send = time.monotonic()
    deadline = next_send + duration_s

    while True:
        now = time.monotonic()
        if now >= deadline:
            break
        if now >= next_send:
            sock.sendto(_sig_pdu(frame_idx), addr)
            frame_idx += 1
            next_send += frame_interval
            if frame_idx % 50 == 0:
                elapsed = frame_idx * frame_interval
                print(f"  {elapsed:.1f}s  ({frame_idx} frames)", end="\r", flush=True)
        else:
            time.sleep(next_send - now)

    sock.sendto(_tx_pdu(TRANSMIT_STATE_ON_NOT_TX), addr)
    print(f"\nON_NOT_TX  ({frame_idx} Signal PDUs, {frame_idx * frame_interval:.1f} s audio)")
    sock.close()


if __name__ == "__main__":
    duration = float(sys.argv[1]) if len(sys.argv) > 1 else 10.0
    main(duration)
