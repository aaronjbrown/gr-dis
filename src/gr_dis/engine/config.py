"""Configuration models and loader for gr-dis."""

from __future__ import annotations

from enum import Enum
from typing import TYPE_CHECKING, Any

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator

if TYPE_CHECKING:
    from pathlib import Path


# Not under TYPE_CHECKING — radio_state.py compares against IdleBehavior at runtime.
class IdleBehavior(str, Enum):
    hard_mute = "hard_mute"
    send_silence = "send_silence"


class LogLevel(str, Enum):
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARN = "WARN"
    ERROR = "ERROR"


class LogFormat(str, Enum):
    json = "json"
    text = "text"


class DuplexMode(str, Enum):
    half = "half"
    full = "full"


class DISConfig(BaseModel):
    version: int = 7
    exercise_id: int = Field(..., ge=1, le=255)
    site_id: int = Field(..., ge=1, le=65534)
    application_id: int = Field(..., ge=1, le=65534)
    multicast: str
    port: int = Field(3000, ge=1, le=65535)
    ttl: int = Field(16, ge=1, le=255)
    source_interface: str = "0.0.0.0"
    loopback: bool = True
    heartbeat_interval_seconds: float = 5.0
    signal_pdu_ms: int = 20
    idle_behavior: IdleBehavior = IdleBehavior.hard_mute

    @field_validator("version")
    @classmethod
    def only_v7(cls, v: int) -> int:
        if v != 7:
            raise ValueError("only DIS version 7 is supported")
        return v

    @field_validator("signal_pdu_ms")
    @classmethod
    def must_be_multiple_of_20(cls, v: int) -> int:
        if v % 20 != 0:
            raise ValueError("signal_pdu_ms must be a multiple of 20")
        return v

    @field_validator("multicast")
    @classmethod
    def valid_multicast(cls, v: str) -> str:
        import ipaddress
        addr = ipaddress.ip_address(v)
        if not addr.is_multicast:
            raise ValueError(f"{v!r} is not a multicast address")
        return v


class BridgeConfig(BaseModel):
    zmq_bind: str = "tcp://127.0.0.1:5555"
    zmq_tx_bind: str | None = None
    metrics_bind: str = "127.0.0.1:9180"
    log_level: LogLevel = LogLevel.INFO
    log_format: LogFormat = LogFormat.json

    @model_validator(mode="after")
    def warn_non_loopback_zmq(self) -> BridgeConfig:
        import ipaddress
        import logging
        import re
        _log = logging.getLogger(__name__)
        for field_name in ("zmq_bind", "zmq_tx_bind"):
            val = getattr(self, field_name)
            if val is None:
                continue
            m = re.match(r"tcp://(?:\[([^\]]+)\]|([^:]+)):", val)
            if m:
                host = m.group(1) if m.group(1) is not None else m.group(2)
                if host not in ("*", "0.0.0.0", "::"):
                    try:
                        addr = ipaddress.ip_address(host)
                        if not addr.is_loopback:
                            _log.warning(
                                "%s is bound to non-loopback %s — ensure the "
                                "network segment is trusted", field_name, host
                            )
                    except ValueError:
                        pass  # hostname; can't validate statically
        return self


class SDRConfig(BaseModel):
    driver: str
    args: dict[str, Any] = Field(default_factory=dict)
    center_freq_hz: float
    sample_rate_hz: float
    gain_db: float | dict[str, float]
    bandwidth_hz: float | None = None
    antenna: str | None = None
    duplex: DuplexMode | None = None


class EntityID(BaseModel):
    site: int = Field(..., ge=0, le=65535)
    app: int = Field(..., ge=0, le=65535)
    entity: int = Field(..., ge=0, le=65535)


class RadioEntityType(BaseModel):
    kind: int = Field(..., ge=0, le=255)
    domain: int = Field(..., ge=0, le=255)
    country: int = Field(..., ge=0, le=65535)
    category: int = Field(..., ge=0, le=255)
    subcategory: int = Field(..., ge=0, le=255)
    specific: int = Field(..., ge=0, le=255)
    extra: int = Field(..., ge=0, le=255)


class RadioConfig(BaseModel):
    radio_id: int = Field(..., ge=1, le=65535)
    entity_id: EntityID
    attached: bool
    relative_antenna_location: list[float] = Field(default_factory=lambda: [0.0, 0.0, 0.0])
    antenna_location_ecef: list[float]
    radio_entity_type: RadioEntityType
    power_dbm: float = 0.0
    input_source: int = 1

    @field_validator("relative_antenna_location", "antenna_location_ecef")
    @classmethod
    def must_be_xyz(cls, v: list[float]) -> list[float]:
        if len(v) != 3:
            raise ValueError("must be a list of exactly 3 floats [x, y, z]")
        return v


class TxFilterConfig(BaseModel):
    entity_id: EntityID
    radio_id: int = Field(..., ge=1, le=65535)


class BandPlanRange(BaseModel):
    from_hz: int
    to_hz: int
    emission_designators: list[str] | None = None
    note: str = ""


class RfTxAuthorizationConfig(BaseModel):
    authorized_ranges: list[BandPlanRange] = Field(default_factory=list)
    # Trusted operator input; must not be derived from network data
    band_plan_file: str | None = None

    @field_validator("band_plan_file")
    @classmethod
    def no_path_traversal(cls, v: str | None) -> str | None:
        if v is not None:
            from pathlib import PurePosixPath
            if ".." in PurePosixPath(v).parts:
                raise ValueError("band_plan_file must not contain '..' components")
        return v


class NBFMChainConfig(BaseModel):
    deviation_hz: float = 5000.0
    audio_lpf_hz: float = 3400.0
    squelch_db: float = -60.0
    squelch_ramp_ms: float = 50.0


class ChannelConfig(BaseModel):
    id: str
    rf_freq_hz: int
    bandwidth_hz: int
    chain: str
    chain_config: dict[str, Any] = Field(default_factory=dict)
    radio: RadioConfig
    tx_enabled: bool = False
    tx_filter: TxFilterConfig | None = None


class CaptureConfig(BaseModel):
    id: str
    zmq_connect: str = "tcp://127.0.0.1:5555"
    zmq_tx_connect: str | None = None
    sdr: SDRConfig
    channels: list[ChannelConfig] = Field(..., min_length=1)


class AppConfig(BaseModel):
    dis: DISConfig
    bridge: BridgeConfig
    captures: list[CaptureConfig] = Field(..., min_length=1)
    rf_tx_authorization: RfTxAuthorizationConfig | None = None

    @model_validator(mode="after")
    def cross_field_validation(self) -> AppConfig:
        channel_ids: list[str] = []
        radio_keys: list[tuple[tuple[int, int, int], int]] = []

        for capture in self.captures:
            sdr = capture.sdr
            half_bw = sdr.sample_rate_hz / 2.0

            for ch in capture.channels:
                # Unique channel IDs across the whole config
                if ch.id in channel_ids:
                    raise ValueError(f"duplicate channel id: {ch.id!r}")
                channel_ids.append(ch.id)

                # RF frequency within SDR Nyquist window
                offset = abs(ch.rf_freq_hz - sdr.center_freq_hz)
                max_offset = half_bw - ch.bandwidth_hz / 2.0
                if offset > max_offset:
                    raise ValueError(
                        f"channel {ch.id!r}: rf_freq_hz {ch.rf_freq_hz} is outside "
                        f"the SDR Nyquist window "
                        f"[{sdr.center_freq_hz - half_bw:.0f}, "
                        f"{sdr.center_freq_hz + half_bw:.0f}]"
                    )

                # Unique (entity_id, radio_id) pairs
                eid = ch.radio.entity_id
                key = ((eid.site, eid.app, eid.entity), ch.radio.radio_id)
                if key in radio_keys:
                    raise ValueError(
                        f"channel {ch.id!r}: duplicate (entity_id, radio_id) "
                        f"({eid.site}/{eid.app}/{eid.entity}, {ch.radio.radio_id})"
                    )
                radio_keys.append(key)

        # TX-related cross-field validation
        any_tx_enabled = any(
            ch.tx_enabled
            for capture in self.captures
            for ch in capture.channels
        )

        if any_tx_enabled and self.bridge.zmq_tx_bind is None:
            raise ValueError(
                "bridge.zmq_tx_bind is required when any channel has tx_enabled: true"
            )

        for capture in self.captures:
            capture_has_tx = any(ch.tx_enabled for ch in capture.channels)
            if capture_has_tx and capture.zmq_tx_connect is None:
                raise ValueError(
                    f"capture {capture.id!r}: zmq_tx_connect required when "
                    f"any channel has tx_enabled: true"
                )
            sdr = capture.sdr
            if capture_has_tx and sdr.duplex is None:
                raise ValueError(
                    f"capture {capture.id!r}: sdr.duplex ('half' or 'full') required "
                    f"when any channel has tx_enabled: true"
                )

        return self


def load_config(path: str | Path) -> AppConfig:
    """Load and validate config from a YAML file."""
    with open(path) as f:
        raw = yaml.safe_load(f)
    return AppConfig.model_validate(raw)
