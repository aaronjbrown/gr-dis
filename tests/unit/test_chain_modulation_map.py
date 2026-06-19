"""Unit tests: RadioChannelHandler sets correct Modulation Type per chain."""

from __future__ import annotations

import socket
import struct
from unittest.mock import MagicMock

import pytest
from prometheus_client import CollectorRegistry

from gr_dis.bridge.radio_state import RadioChannelHandler
from gr_dis.engine.config import AppConfig
from gr_dis.metrics import BridgeMetrics

_BASE_CONFIG = {
    "dis": {
        "exercise_id": 1, "site_id": 1, "application_id": 100,
        "multicast": "239.1.2.3",
    },
    "bridge": {},
    "captures": [{
        "id": "cap0",
        "sdr": {
            "driver": "rtlsdr",
            "center_freq_hz": 107_300_000,
            "sample_rate_hz": 2_400_000,
            "gain_db": 20,
        },
        "channels": [{
            "id": "ch0",
            "rf_freq_hz": 107_700_000,
            "bandwidth_hz": 200_000,
            "chain": "nbfm",  # overridden per test
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
}


def _handler_for_chain(chain: str) -> tuple[RadioChannelHandler, list[bytes]]:
    import copy
    cfg_dict = copy.deepcopy(_BASE_CONFIG)
    cfg_dict["captures"][0]["channels"][0]["chain"] = chain
    config = AppConfig.model_validate(cfg_dict)
    sent: list[bytes] = []
    mock_sock = MagicMock(spec=socket.socket)
    mock_sock.sendto.side_effect = lambda data, addr: sent.append(bytes(data))
    metrics = BridgeMetrics(registry=CollectorRegistry())
    channel = config.captures[0].channels[0]
    return RadioChannelHandler(channel, config.dis, mock_sock, metrics), sent


def _modulation_fields(pdu: bytes) -> tuple[int, int, int, int]:
    """Extract (spread, major, detail, system) from a Transmitter PDU."""
    return struct.unpack_from(">HHHH", pdu, 88)


@pytest.mark.parametrize("chain,expected", [
    ("nbfm", (0, 3, 1, 1)),  # Angle / FM (Angle) / Generic
    ("wfm",  (0, 3, 1, 1)),  # Angle / FM (Angle) / Generic
])
def test_modulation_type_by_chain(chain: str, expected: tuple[int, int, int, int]) -> None:
    handler, sent = _handler_for_chain(chain)
    handler.send_transmitter_pdu()
    assert len(sent) == 1
    assert _modulation_fields(sent[0]) == expected


def test_unknown_chain_falls_back_to_nbfm_defaults() -> None:
    """An unrecognised chain name should not raise; it gets NBFM defaults."""
    handler, sent = _handler_for_chain("future_chain")
    handler.send_transmitter_pdu()
    assert len(sent) == 1
    spread, major, detail, system = _modulation_fields(sent[0])
    assert major == 3   # Angle / FM (NBFM fallback)
    assert detail == 1  # FM (Angle)
