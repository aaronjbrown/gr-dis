#!/usr/bin/env python3
"""Wrap golden PDU .bin files in pcap UDP frames and validate with tshark.

Usage:
    python3 scripts/golden-pdu-validate.py [--fixtures DIR]
"""

from __future__ import annotations

import argparse
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

_GOLDEN_DIR = Path(__file__).parents[1] / "tests" / "fixtures" / "golden_pdus"

# DIS network parameters matching examples/config.example.yaml
_SRC_IP = bytes([192, 168, 1, 1])
_DST_IP = bytes([239, 1, 2, 3])  # multicast
_SRC_PORT = 12345
_DIS_PORT = 3000


def _ip_checksum(data: bytes) -> int:
    total = 0
    for i in range(0, len(data), 2):
        word = (data[i] << 8) + (data[i + 1] if i + 1 < len(data) else 0)
        total += word
    total = (total >> 16) + (total & 0xFFFF)
    total += total >> 16
    return (~total) & 0xFFFF


def _build_packet(payload: bytes) -> bytes:
    """Wrap payload in Ethernet/IPv4/UDP — minimal, checksum-correct."""
    udp_len = 8 + len(payload)
    ip_len = 20 + udp_len

    udp = struct.pack(">HHHH", _SRC_PORT, _DIS_PORT, udp_len, 0)

    ip_raw = struct.pack(
        ">BBHHHBBH4s4s",
        0x45, 0, ip_len, 0, 0, 64, 17, 0, _SRC_IP, _DST_IP,
    )
    cs = _ip_checksum(ip_raw)
    ip = ip_raw[:10] + struct.pack(">H", cs) + ip_raw[12:]

    # Multicast MAC for 239.1.2.3 → 01:00:5e:01:02:03
    eth = bytes([0x01, 0x00, 0x5E, 0x01, 0x02, 0x03,
                 0x00, 0x00, 0x00, 0x00, 0x00, 0x01,
                 0x08, 0x00])
    return eth + ip + udp + payload


def _write_pcap(path: Path, packet: bytes) -> None:
    """Write a single-packet pcap file (link type 1 = Ethernet)."""
    with open(path, "wb") as f:
        # Global header
        f.write(struct.pack("<IHHiIII", 0xA1B2C3D4, 2, 4, 0, 0, 65535, 1))
        # Packet record
        f.write(struct.pack("<IIII", 0, 0, len(packet), len(packet)))
        f.write(packet)


def validate(label: str, bin_path: Path, verbose: bool) -> bool:
    payload = bin_path.read_bytes()
    packet = _build_packet(payload)

    with tempfile.NamedTemporaryFile(suffix=".pcap", delete=False) as tmp:
        tmp_path = Path(tmp.name)

    try:
        _write_pcap(tmp_path, packet)

        cmd = ["tshark", "-r", str(tmp_path), "-V", "-O", "dis"]
        result = subprocess.run(cmd, capture_output=True, text=True)

        if verbose:
            print(f"\n{'='*60}")
            print(f"  {label}  ({len(payload)} bytes)")
            print(f"{'='*60}")
            print(result.stdout)
            if result.stderr:
                print("[stderr]", result.stderr, file=sys.stderr)

        if result.returncode != 0:
            print(f"FAIL [{label}]: tshark exited {result.returncode}", file=sys.stderr)
            return False

        # Check tshark actually found a DIS PDU
        if "Distributed Interactive Simulation" not in result.stdout:
            print(f"FAIL [{label}]: no DIS dissection in tshark output", file=sys.stderr)
            if not verbose:
                print(result.stdout)
            return False

        if not verbose:
            print(f"OK   {label}")
        return True

    finally:
        tmp_path.unlink(missing_ok=True)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--fixtures", type=Path, default=_GOLDEN_DIR,
                    help="Directory containing *.bin golden PDU files")
    ap.add_argument("-v", "--verbose", action="store_true",
                    help="Print full tshark -V output for each PDU")
    args = ap.parse_args()

    bins = sorted(args.fixtures.glob("*.bin"))
    if not bins:
        print(f"No .bin files found in {args.fixtures}", file=sys.stderr)
        return 1

    failures = 0
    for bin_path in bins:
        ok = validate(bin_path.name, bin_path, args.verbose)
        if not ok:
            failures += 1

    if failures:
        print(f"\n{failures}/{len(bins)} fixture(s) FAILED tshark validation.",
              file=sys.stderr)
        return 1

    print(f"\nAll {len(bins)} golden PDU(s) validated successfully.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
