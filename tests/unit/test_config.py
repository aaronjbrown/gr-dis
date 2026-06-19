"""Unit tests for configuration loading and validation."""

from __future__ import annotations

import copy
from pathlib import Path

import pytest
from pydantic import ValidationError

from gr_dis.engine.config import (
    AppConfig,
    BandPlanRange,
    DuplexMode,
    RfTxAuthorizationConfig,  # noqa: F401
    TxFilterConfig,  # noqa: F401
    load_config,
)

EXAMPLES = Path(__file__).parents[2] / "examples"
FIXTURES = Path(__file__).parent / "fixtures"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _minimal_raw(**overrides: object) -> dict:
    """Return the minimal valid raw config dict, with optional overrides merged."""
    cfg: dict = {
        "dis": {
            "exercise_id": 1,
            "site_id": 1,
            "application_id": 100,
            "multicast": "239.1.2.3",
        },
        "bridge": {},
        "captures": [
            {
                "id": "cap0",
                "sdr": {
                    "driver": "rtlsdr",
                    "center_freq_hz": 145_500_000,
                    "sample_rate_hz": 2_400_000,
                    "gain_db": 20,
                },
                "channels": [
                    {
                        "id": "ch0",
                        "rf_freq_hz": 145_500_000,
                        "bandwidth_hz": 25_000,
                        "chain": "nbfm",
                        "radio": {
                            "radio_id": 1,
                            "entity_id": {"site": 1, "app": 100, "entity": 5001},
                            "attached": False,
                            "antenna_location_ecef": [3_875_000.0, 332_000.0, 5_025_000.0],
                            "radio_entity_type": {
                                "kind": 7,
                                "domain": 3,
                                "country": 225,
                                "category": 1,
                                "subcategory": 0,
                                "specific": 0,
                                "extra": 0,
                            },
                        },
                    }
                ],
            }
        ],
    }
    cfg.update(overrides)
    return cfg


# _BASE is a module-level snapshot of the minimal valid config dict used by TX tests.
_BASE = _minimal_raw()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestExampleConfig:
    def test_example_config_loads(self) -> None:
        cfg = load_config(EXAMPLES / "config.example.yaml")
        assert cfg.dis.exercise_id == 1
        assert len(cfg.captures) == 1
        assert len(cfg.captures[0].channels) == 2
        assert cfg.captures[0].channels[0].id == "vhf_ch1"
        assert cfg.captures[0].channels[1].id == "vhf_ch2"


class TestDISValidation:
    def test_invalid_version(self) -> None:
        raw = _minimal_raw()
        raw["dis"]["version"] = 6  # type: ignore[index]
        with pytest.raises(ValidationError, match="version 7"):
            AppConfig.model_validate(raw)

    def test_exercise_id_out_of_range(self) -> None:
        raw = _minimal_raw()
        raw["dis"]["exercise_id"] = 0  # type: ignore[index]
        with pytest.raises(ValidationError) as exc:
            AppConfig.model_validate(raw)
        assert "exercise_id" in str(exc.value)

    def test_exercise_id_too_large(self) -> None:
        raw = _minimal_raw()
        raw["dis"]["exercise_id"] = 256  # type: ignore[index]
        with pytest.raises(ValidationError) as exc:
            AppConfig.model_validate(raw)
        assert "exercise_id" in str(exc.value)

    def test_signal_pdu_ms_not_multiple_of_20(self) -> None:
        raw = _minimal_raw()
        raw["dis"]["signal_pdu_ms"] = 30  # type: ignore[index]
        with pytest.raises(ValidationError, match="multiple of 20"):
            AppConfig.model_validate(raw)

    def test_non_multicast_address(self) -> None:
        raw = _minimal_raw()
        raw["dis"]["multicast"] = "192.168.1.1"  # type: ignore[index]
        with pytest.raises(ValidationError, match="multicast"):
            AppConfig.model_validate(raw)

    def test_valid_signal_pdu_ms(self) -> None:
        raw = _minimal_raw()
        raw["dis"]["signal_pdu_ms"] = 60  # type: ignore[index]
        cfg = AppConfig.model_validate(raw)
        assert cfg.dis.signal_pdu_ms == 60


class TestChannelValidation:
    def test_duplicate_channel_id(self) -> None:
        raw = _minimal_raw()
        ch = raw["captures"][0]["channels"][0]  # type: ignore[index]
        raw["captures"][0]["channels"].append(dict(ch, radio={  # type: ignore[index]
            **ch["radio"],
            "radio_id": 2,
            "entity_id": {"site": 1, "app": 100, "entity": 5002},
        }))
        # Both channels have id "ch0"
        with pytest.raises(ValidationError, match="duplicate channel id"):
            AppConfig.model_validate(raw)

    def test_duplicate_radio_key(self) -> None:
        raw = _minimal_raw()
        ch = raw["captures"][0]["channels"][0]  # type: ignore[index]
        raw["captures"][0]["channels"].append({  # type: ignore[index]
            "id": "ch1",
            "rf_freq_hz": 145_510_000,
            "bandwidth_hz": 25_000,
            "chain": "nbfm",
            "radio": dict(ch["radio"]),  # same radio_id + entity_id
        })
        with pytest.raises(ValidationError, match="duplicate.*radio_id"):
            AppConfig.model_validate(raw)

    def test_rf_freq_outside_nyquist(self) -> None:
        raw = _minimal_raw()
        # center=145.5 MHz, sr=2.4 MHz → window [144.3, 146.7]; put freq at 148 MHz
        raw["captures"][0]["channels"][0]["rf_freq_hz"] = 148_000_000  # type: ignore[index]
        with pytest.raises(ValidationError, match="Nyquist"):
            AppConfig.model_validate(raw)

    def test_rf_freq_inside_nyquist(self) -> None:
        raw = _minimal_raw()
        raw["captures"][0]["channels"][0]["rf_freq_hz"] = 145_600_000  # type: ignore[index]
        cfg = AppConfig.model_validate(raw)
        assert cfg.captures[0].channels[0].rf_freq_hz == 145_600_000


class TestMinimalConfig:
    def test_minimal_valid_config(self) -> None:
        cfg = AppConfig.model_validate(_minimal_raw())
        assert cfg.dis.version == 7
        assert cfg.bridge.zmq_bind == "tcp://127.0.0.1:5555"
        assert cfg.captures[0].channels[0].radio.attached is False


# ---------------------------------------------------------------------------
# TX config helpers and tests
# ---------------------------------------------------------------------------

def _tx_config(**overrides: object) -> dict:
    cfg = copy.deepcopy(_BASE)
    cfg["captures"][0]["channels"][0]["tx_enabled"] = True
    cfg["captures"][0]["sdr"]["duplex"] = "half"
    cfg["captures"][0]["zmq_tx_connect"] = "tcp://127.0.0.1:5556"
    cfg["bridge"]["zmq_tx_bind"] = "tcp://127.0.0.1:5556"
    cfg.update(overrides)
    return cfg


def test_duplex_mode_enum() -> None:
    assert DuplexMode.half == "half"
    assert DuplexMode.full == "full"


def test_tx_enabled_false_by_default() -> None:
    cfg = AppConfig.model_validate(_BASE)
    assert cfg.captures[0].channels[0].tx_enabled is False


def test_tx_enabled_requires_zmq_tx_bind() -> None:
    cfg = copy.deepcopy(_BASE)
    cfg["captures"][0]["channels"][0]["tx_enabled"] = True
    cfg["captures"][0]["sdr"]["duplex"] = "half"
    cfg["captures"][0]["zmq_tx_connect"] = "tcp://127.0.0.1:5556"
    # bridge.zmq_tx_bind missing (it must be explicitly set)
    with pytest.raises(ValueError, match="zmq_tx_bind"):
        AppConfig.model_validate(cfg)


def test_tx_enabled_requires_zmq_tx_connect() -> None:
    cfg = copy.deepcopy(_BASE)
    cfg["captures"][0]["channels"][0]["tx_enabled"] = True
    cfg["captures"][0]["sdr"]["duplex"] = "half"
    cfg["bridge"]["zmq_tx_bind"] = "tcp://127.0.0.1:5556"
    # capture zmq_tx_connect missing
    with pytest.raises(ValueError, match="zmq_tx_connect"):
        AppConfig.model_validate(cfg)


def test_tx_enabled_requires_duplex() -> None:
    cfg = copy.deepcopy(_BASE)
    cfg["captures"][0]["channels"][0]["tx_enabled"] = True
    cfg["bridge"]["zmq_tx_bind"] = "tcp://127.0.0.1:5556"
    cfg["captures"][0]["zmq_tx_connect"] = "tcp://127.0.0.1:5556"
    # sdr.duplex missing
    with pytest.raises(ValueError, match="duplex"):
        AppConfig.model_validate(cfg)


def test_valid_tx_config_passes() -> None:
    cfg = AppConfig.model_validate(_tx_config())
    ch = cfg.captures[0].channels[0]
    assert ch.tx_enabled is True
    assert cfg.captures[0].sdr.duplex == DuplexMode.half
    assert cfg.bridge.zmq_tx_bind == "tcp://127.0.0.1:5556"


def test_tx_filter_optional() -> None:
    cfg_dict = _tx_config()
    cfg_dict["captures"][0]["channels"][0]["tx_filter"] = {
        "entity_id": {"site": 1, "app": 200, "entity": 42},
        "radio_id": 5,
    }
    cfg = AppConfig.model_validate(cfg_dict)
    assert cfg.captures[0].channels[0].tx_filter is not None
    assert cfg.captures[0].channels[0].tx_filter.radio_id == 5


def test_rf_tx_authorization_optional() -> None:
    cfg_dict = _tx_config()
    cfg_dict["rf_tx_authorization"] = {
        "authorized_ranges": [
            {"from_hz": 144_000_000, "to_hz": 148_000_000,
             "emission_designators": ["16K0F3E"], "note": "2m band"},
        ]
    }
    cfg = AppConfig.model_validate(cfg_dict)
    assert cfg.rf_tx_authorization is not None
    assert len(cfg.rf_tx_authorization.authorized_ranges) == 1


def test_band_plan_range_no_emission_designators() -> None:
    r = BandPlanRange.model_validate({"from_hz": 1_000_000, "to_hz": 2_000_000})
    assert r.emission_designators is None


class TestRfTxAuthorizationBandPlanFile:
    def test_band_plan_file_with_path_traversal_rejected(self) -> None:
        """band_plan_file with '..' components should raise ValidationError."""
        with pytest.raises(ValidationError, match="band_plan_file must not contain"):
            RfTxAuthorizationConfig.model_validate({"band_plan_file": "../etc/passwd"})

    def test_band_plan_file_with_traversal_in_middle_rejected(self) -> None:
        """band_plan_file with '..' in the middle should raise ValidationError."""
        with pytest.raises(ValidationError, match="band_plan_file must not contain"):
            RfTxAuthorizationConfig.model_validate({"band_plan_file": "/home/../etc/passwd"})

    def test_band_plan_file_valid_absolute_path_passes(self) -> None:
        """band_plan_file with a valid absolute path should pass."""
        cfg = RfTxAuthorizationConfig.model_validate({"band_plan_file": "/etc/band_plan.txt"})
        assert cfg.band_plan_file == "/etc/band_plan.txt"

    def test_band_plan_file_valid_relative_path_passes(self) -> None:
        """band_plan_file with a valid relative path should pass."""
        cfg = RfTxAuthorizationConfig.model_validate({"band_plan_file": "config/band_plan.txt"})
        assert cfg.band_plan_file == "config/band_plan.txt"

    def test_band_plan_file_none_passes(self) -> None:
        """band_plan_file with None should pass."""
        cfg = RfTxAuthorizationConfig.model_validate({"band_plan_file": None})
        assert cfg.band_plan_file is None

    def test_band_plan_file_omitted_passes(self) -> None:
        """band_plan_file omitted should pass (defaults to None)."""
        cfg = RfTxAuthorizationConfig.model_validate({})
        assert cfg.band_plan_file is None


# ---------------------------------------------------------------------------
# ZMQ bind address warning tests
# ---------------------------------------------------------------------------

class TestZmqBindWarning:
    def test_wildcard_zmq_bind_silently_skipped(self, caplog: pytest.LogCaptureFixture) -> None:
        """0.0.0.0 is on the wildcard skip-list; no warning should be emitted."""
        import logging

        from gr_dis.engine.config import BridgeConfig

        with caplog.at_level(logging.WARNING, logger="gr_dis.engine.config"):
            BridgeConfig(zmq_bind="tcp://0.0.0.0:5555")

        assert not any("zmq_bind" in r.message for r in caplog.records)

    def test_non_loopback_specific_ip_warns(self, caplog: pytest.LogCaptureFixture) -> None:
        """A zmq_bind set to a specific non-loopback IP should emit a warning."""
        import logging

        from gr_dis.engine.config import BridgeConfig

        with caplog.at_level(logging.WARNING, logger="gr_dis.engine.config"):
            BridgeConfig(zmq_bind="tcp://192.168.1.10:5555")

        assert any("zmq_bind" in r.message for r in caplog.records)
        assert any("192.168.1.10" in r.message for r in caplog.records)

    def test_loopback_zmq_bind_no_warning(self, caplog: pytest.LogCaptureFixture) -> None:
        """A zmq_bind on loopback should NOT emit a warning."""
        import logging

        from gr_dis.engine.config import BridgeConfig

        with caplog.at_level(logging.WARNING, logger="gr_dis.engine.config"):
            BridgeConfig(zmq_bind="tcp://127.0.0.1:5555")

        assert not any("zmq_bind" in r.message for r in caplog.records)

    def test_non_loopback_zmq_tx_bind_warns(self, caplog: pytest.LogCaptureFixture) -> None:
        """A zmq_tx_bind set to a specific non-loopback IP should emit a warning."""
        import logging

        from gr_dis.engine.config import BridgeConfig

        with caplog.at_level(logging.WARNING, logger="gr_dis.engine.config"):
            BridgeConfig(zmq_tx_bind="tcp://10.0.0.1:5556")

        assert any("zmq_tx_bind" in r.message for r in caplog.records)
        assert any("10.0.0.1" in r.message for r in caplog.records)

    def test_zmq_tx_bind_none_no_warning(self, caplog: pytest.LogCaptureFixture) -> None:
        """A None zmq_tx_bind should not trigger any warning."""
        import logging

        from gr_dis.engine.config import BridgeConfig

        with caplog.at_level(logging.WARNING, logger="gr_dis.engine.config"):
            BridgeConfig(zmq_tx_bind=None)

        assert not any("zmq_tx_bind" in r.message for r in caplog.records)

    def test_non_loopback_ipv6_zmq_bind_warns(self, caplog: pytest.LogCaptureFixture) -> None:
        """A zmq_bind with a bracketed non-loopback IPv6 address should warn."""
        import logging

        from gr_dis.engine.config import BridgeConfig

        with caplog.at_level(logging.WARNING, logger="gr_dis.engine.config"):
            BridgeConfig(zmq_bind="tcp://[2001:db8::1]:5555")

        assert any("zmq_bind" in r.message for r in caplog.records)
