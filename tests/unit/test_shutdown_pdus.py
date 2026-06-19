"""Unit tests: RadioChannelHandler.send_off_transmitter_pdu."""

from __future__ import annotations

import socket
from unittest.mock import MagicMock

from prometheus_client import CollectorRegistry

from gr_dis.bridge.pdu.enums import TRANSMIT_STATE_OFF, TRANSMIT_STATE_ON_NOT_TX
from gr_dis.bridge.radio_state import RadioChannelHandler
from gr_dis.engine.config import AppConfig
from gr_dis.metrics import BridgeMetrics


def _make_handler() -> tuple[RadioChannelHandler, list[bytes]]:
    sent: list[bytes] = []
    mock_sock = MagicMock(spec=socket.socket)
    mock_sock.sendto.side_effect = lambda data, addr: sent.append(bytes(data))
    config = AppConfig.model_validate({
        "dis": {
            "exercise_id": 1, "site_id": 1, "application_id": 100,
            "multicast": "239.1.2.3",
        },
        "bridge": {},
        "captures": [{
            "id": "cap0",
            "sdr": {
                "driver": "rtlsdr",
                "center_freq_hz": 145_500_000,
                "sample_rate_hz": 2_400_000,
                "gain_db": 20,
            },
            "channels": [{
                "id": "ch0", "rf_freq_hz": 145_500_000, "bandwidth_hz": 25_000, "chain": "nbfm",
                "radio": {
                    "radio_id": 1,
                    "entity_id": {"site": 1, "app": 100, "entity": 5001},
                    "attached": False,
                    "antenna_location_ecef": [3_875_000.0, 332_000.0, 5_025_000.0],
                    "radio_entity_type": {
                        "kind": 7, "domain": 3, "country": 225,
                        "category": 1, "subcategory": 0, "specific": 0, "extra": 0,
                    },
                },
            }],
        }],
    })
    channel = config.captures[0].channels[0]
    metrics = BridgeMetrics(registry=CollectorRegistry())
    return RadioChannelHandler(channel, config.dis, mock_sock, metrics), sent


def test_send_off_transmitter_pdu_byte_is_zero() -> None:
    handler, sent = _make_handler()
    handler.send_off_transmitter_pdu()
    assert len(sent) == 1
    assert sent[0][28] == TRANSMIT_STATE_OFF


def test_send_off_transmitter_pdu_does_not_change_internal_state() -> None:
    handler, _ = _make_handler()
    assert handler._tx_state.transmit_state == TRANSMIT_STATE_ON_NOT_TX
    handler.send_off_transmitter_pdu()
    assert handler._tx_state.transmit_state == TRANSMIT_STATE_ON_NOT_TX
