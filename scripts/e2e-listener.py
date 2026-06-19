#!/usr/bin/env python3
"""Subscribe to the DIS multicast group and dump received PDUs.

Use this together with ``gr-dis run --source-file ...`` for an end-to-end
demo: the capture demods the fixture, the bridge emits Transmitter+Signal
PDUs, this listener prints them with their key DIS fields decoded.
"""

from __future__ import annotations

import argparse
import socket
import struct
import sys
import time

PDU_TYPE_NAMES = {1: "EntityState", 25: "Transmitter", 26: "Signal"}
TX_STATE_NAMES = {0: "Off", 1: "OnNotTx", 2: "OnTx"}


def decode_common_header(buf: bytes) -> dict[str, int]:
    # 12-byte DIS PDU header (v7): protocol_version, exercise_id, pdu_type,
    # protocol_family, timestamp, length, pdu_status, padding
    pv, ex, pt, pf, ts, ln, st, _pad = struct.unpack(">BBBBIHBB", buf[:12])
    return {
        "protocol_version": pv,
        "exercise_id": ex,
        "pdu_type": pt,
        "pdu_family": pf,
        "timestamp": ts,
        "length": ln,
        "pdu_status": st,
    }


def decode_transmitter_body(buf: bytes) -> dict[str, int]:
    # Offset 12: HHHH entity_id (site, app, entity, radio_id) = 8 bytes
    site, app, entity, radio_id = struct.unpack(">HHHH", buf[12:20])
    # Offset 20: BBHBBBB radio entity type (8 bytes)
    # Offset 28: B transmit_state
    transmit_state = buf[28]
    return {
        "site": site,
        "app": app,
        "entity": entity,
        "radio_id": radio_id,
        "transmit_state": transmit_state,
    }


def decode_signal_body(buf: bytes) -> dict[str, int]:
    # 12-byte header + 12-byte signal body (entity + radio_id) then the
    # encoding scheme / TDL / sample rate / sample count / pad. Lay it out
    # per IEEE 1278.1-2012 §6.2.94:
    #   header(12) entity_id(8) radio_id(2) ... (the bridge puts radio_id at
    #   offset 18 per its build_signal_pdu code path)
    site, app, entity, radio_id = struct.unpack(">HHHH", buf[12:20])
    encoding = struct.unpack(">H", buf[20:22])[0]
    return {
        "site": site,
        "app": app,
        "entity": entity,
        "radio_id": radio_id,
        "encoding": encoding,
        "payload_bytes": len(buf) - 32,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--group", default="239.1.2.3")
    ap.add_argument("--port", type=int, default=3000)
    ap.add_argument("--duration", type=float, default=10.0)
    ap.add_argument("--max-pdus", type=int, default=500)
    args = ap.parse_args()

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("0.0.0.0", args.port))
    mreq = struct.pack("4sL", socket.inet_aton(args.group), socket.INADDR_ANY)
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
    sock.settimeout(0.25)

    t0 = time.monotonic()
    counts: dict[int, int] = {}
    tx_state_observed: set[int] = set()
    first_signal_payload_size: int | None = None

    print(f"# listening on {args.group}:{args.port} for {args.duration}s")
    print(f"{'t(s)':>5}  {'type':>11}  details")
    print("-" * 70)

    while time.monotonic() - t0 < args.duration and sum(counts.values()) < args.max_pdus:
        try:
            data, _ = sock.recvfrom(65535)
        except TimeoutError:
            continue

        h = decode_common_header(data)
        t = time.monotonic() - t0
        type_name = PDU_TYPE_NAMES.get(h["pdu_type"], f"#{h['pdu_type']}")
        counts[h["pdu_type"]] = counts.get(h["pdu_type"], 0) + 1

        if h["pdu_type"] == 25:  # Transmitter
            tx = decode_transmitter_body(data)
            tx_state_observed.add(tx["transmit_state"])
            state = TX_STATE_NAMES.get(tx["transmit_state"], "?")
            print(
                f"{t:5.2f}  {type_name:>11}  "
                f"radio={tx['site']}/{tx['app']}/{tx['entity']}.{tx['radio_id']}  "
                f"tx_state={tx['transmit_state']}({state})  len={h['length']}"
            )
        elif h["pdu_type"] == 26:  # Signal
            sg = decode_signal_body(data)
            if first_signal_payload_size is None:
                first_signal_payload_size = sg["payload_bytes"]
            if counts[26] <= 5 or counts[26] % 25 == 0:
                print(
                    f"{t:5.2f}  {type_name:>11}  "
                    f"radio={sg['site']}/{sg['app']}/{sg['entity']}.{sg['radio_id']}  "
                    f"encoding=0x{sg['encoding']:04x}  payload={sg['payload_bytes']}B  "
                    f"len={h['length']}"
                )

    sock.close()

    print()
    print("=" * 70)
    print("Summary:")
    for pt, c in sorted(counts.items()):
        print(f"  {PDU_TYPE_NAMES.get(pt, f'#{pt}'):>11}: {c} PDUs")
    print(f"  Transmit states observed: {sorted(tx_state_observed)}")
    if first_signal_payload_size is not None:
        print(f"  First Signal payload size: {first_signal_payload_size} bytes "
              f"({first_signal_payload_size} samples @ μ-law)")
    return 0 if counts else 1


if __name__ == "__main__":
    sys.exit(main())
