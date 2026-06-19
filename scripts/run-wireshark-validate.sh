#!/usr/bin/env bash
# Validate golden PDU .bin files with tshark.
# Run after: pytest tests/unit/test_pdu_*.py  (which writes the .bin files)
#
# Install tshark:  sudo dnf install wireshark-cli   (Fedora)
#                  sudo apt install tshark           (Debian/Ubuntu)

set -euo pipefail

GOLDEN_DIR="$(dirname "$0")/../tests/fixtures/golden_pdus"
ERRORS=0

validate() {
    local label="$1"
    local file="$2"

    echo "=== $label ==="
    if ! tshark -r "$file" -V -O dis 2>&1; then
        echo "FAIL: tshark returned non-zero for $label" >&2
        ERRORS=$((ERRORS + 1))
    fi
    echo
}

validate "Transmitter (standalone)" "$GOLDEN_DIR/transmitter_standalone.bin"
validate "Transmitter (attached)"   "$GOLDEN_DIR/transmitter_attached.bin"
validate "Signal 20ms"              "$GOLDEN_DIR/signal_20ms.bin"
validate "Signal 60ms"              "$GOLDEN_DIR/signal_60ms.bin"

if [[ "$ERRORS" -gt 0 ]]; then
    echo "FAILED: $ERRORS fixture(s) failed tshark validation" >&2
    exit 1
fi
echo "All golden PDUs validated by tshark."
