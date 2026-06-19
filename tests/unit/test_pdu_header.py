"""Unit tests for the common 12-byte DIS PDU header."""

from __future__ import annotations

import struct

from gr_dis.bridge.pdu.enums import PDU_TYPE_SIGNAL, PDU_TYPE_TRANSMITTER
from gr_dis.bridge.pdu.header import pack_header, pdu_status


def _unpack_header(data: bytes) -> dict:
    version, exercise_id, pdu_type, family, timestamp, length, status, padding = (
        struct.unpack(">BBBBIHBB", data[:12])
    )
    return dict(
        version=version,
        exercise_id=exercise_id,
        pdu_type=pdu_type,
        family=family,
        timestamp=timestamp,
        length=length,
        status=status,
        padding=padding,
    )


class TestPduStatus:
    def test_unattached_rai(self) -> None:
        # RAI = 1 (unattached) → bits 5..4 = 01 → byte = 0x10
        assert pdu_status(False) == 0x10

    def test_attached_rai(self) -> None:
        # RAI = 2 (attached) → bits 5..4 = 10 → byte = 0x20
        assert pdu_status(True) == 0x20


class TestPackHeader:
    def test_length_is_12(self) -> None:
        h = pack_header(PDU_TYPE_TRANSMITTER, 1, 104, False, 1)
        assert len(h) == 12

    def test_version_field(self) -> None:
        h = pack_header(PDU_TYPE_TRANSMITTER, 1, 104, False, 1)
        assert _unpack_header(h)["version"] == 7

    def test_protocol_family(self) -> None:
        h = pack_header(PDU_TYPE_TRANSMITTER, 1, 104, False, 1)
        assert _unpack_header(h)["family"] == 4

    def test_exercise_id(self) -> None:
        h = pack_header(PDU_TYPE_TRANSMITTER, 42, 104, False, 1)
        assert _unpack_header(h)["exercise_id"] == 42

    def test_pdu_type_transmitter(self) -> None:
        h = pack_header(PDU_TYPE_TRANSMITTER, 1, 104, False, 1)
        assert _unpack_header(h)["pdu_type"] == 25

    def test_pdu_type_signal(self) -> None:
        h = pack_header(PDU_TYPE_SIGNAL, 1, 192, False, 1)
        assert _unpack_header(h)["pdu_type"] == 26

    def test_pdu_length_field(self) -> None:
        h = pack_header(PDU_TYPE_SIGNAL, 1, 192, True, 1)
        assert _unpack_header(h)["length"] == 192

    def test_timestamp_field(self) -> None:
        h = pack_header(PDU_TYPE_TRANSMITTER, 1, 104, False, 0xDEADBEEF)
        assert _unpack_header(h)["timestamp"] == 0xDEADBEEF

    def test_padding_zero(self) -> None:
        h = pack_header(PDU_TYPE_TRANSMITTER, 1, 104, False, 1)
        assert _unpack_header(h)["padding"] == 0

    def test_rai_unattached_in_status(self) -> None:
        h = pack_header(PDU_TYPE_TRANSMITTER, 1, 104, False, 1)
        assert _unpack_header(h)["status"] == 0x10

    def test_rai_attached_in_status(self) -> None:
        h = pack_header(PDU_TYPE_TRANSMITTER, 1, 104, True, 1)
        assert _unpack_header(h)["status"] == 0x20

    def test_big_endian_length(self) -> None:
        # Length = 104 = 0x0068; big-endian at bytes 8-9
        h = pack_header(PDU_TYPE_TRANSMITTER, 1, 104, False, 1)
        assert h[8:10] == bytes([0x00, 0x68])
