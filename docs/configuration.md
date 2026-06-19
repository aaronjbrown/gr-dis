# Configuration reference

`gr-dis` is configured with a single YAML file. Pass it to any command with `--config path/to/config.yaml`.

Validate without running the bridge:

```bash
gr-dis validate --config config.yaml
```

A fully populated example lives at [`examples/config.example.yaml`](../examples/config.example.yaml).

---

## Top-level structure

```yaml
dis:                  # DIS network and exercise binding (required)
bridge:               # Bridge process settings (required)
captures:             # List of SDR capture sessions (required, ≥ 1)
rf_tx_authorization:  # RF TX band plan (optional; restricts TX path)
```

---

## `dis`

| Field | Type | Default | Notes |
|---|---|---|---|
| `version` | int | `7` | Only DIS v7 is supported |
| `exercise_id` | int 1–255 | required | From the exercise operator |
| `site_id` | int 1–65534 | required | From the exercise operator |
| `application_id` | int 1–65534 | required | From the exercise operator |
| `multicast` | IPv4 string | required | e.g. `239.1.2.3` |
| `port` | int 1–65535 | `3000` | DIS UDP port |
| `ttl` | int 1–255 | `16` | Multicast TTL |
| `source_interface` | IPv4 string | `0.0.0.0` | Outbound NIC; `0.0.0.0` = any |
| `loopback` | bool | `true` | `IP_MULTICAST_LOOP` — set `false` in production if loopback is unwanted |
| `heartbeat_interval_seconds` | float | `5.0` | Transmitter PDU heartbeat period |
| `signal_pdu_ms` | int | `20` | Audio duration per Signal PDU (must be a multiple of 20) |
| `idle_behavior` | enum | `hard_mute` | `hard_mute` (no Signal PDUs when squelch is closed) or `send_silence` (emit zero-amplitude Signal PDUs) |

---

## `bridge`

| Field | Type | Default | Notes |
|---|---|---|---|
| `zmq_bind` | ZMQ endpoint | `tcp://127.0.0.1:5555` | Bridge SUB socket — Captures connect to this |
| `zmq_tx_bind` | ZMQ endpoint | `null` | TX PUB socket — set to enable the TX path (e.g. `tcp://127.0.0.1:5556`) |
| `metrics_bind` | host:port | `127.0.0.1:9180` | Prometheus `/metrics` and `/healthz` |
| `log_level` | enum | `INFO` | `DEBUG`, `INFO`, `WARN`, or `ERROR` |
| `log_format` | enum | `json` | `json` (structured) or `text` |

---

## `captures[]`

Each entry maps to one GR flowgraph process and one SDR session.

| Field | Type | Default | Notes |
|---|---|---|---|
| `id` | string | required | Unique across all captures |
| `zmq_connect` | ZMQ endpoint | matches `bridge.zmq_bind` | Where this Capture publishes RX audio |
| `zmq_tx_connect` | ZMQ endpoint | `null` | TX audio feed from the Bridge — must match `bridge.zmq_tx_bind` |
| `sdr` | object | required | See below |
| `channels` | list | required, ≥ 1 | See below |

### `captures[].sdr`

| Field | Type | Notes |
|---|---|---|
| `driver` | string | SoapySDR driver name: `rtlsdr`, `hackrf`, `uhd`, `lime`, … |
| `args` | object | Extra SoapySDR args, e.g. `{ serial: "00000001" }` |
| `center_freq_hz` | float | SDR LO frequency in Hz |
| `sample_rate_hz` | float | Sample rate in Hz — must cover all channel offsets within Nyquist |
| `gain_db` | float or object | Scalar (overall gain) or per-stage map, e.g. `{ LNA: 20, VGA: 10 }` |
| `bandwidth_hz` | float or null | Analog filter bandwidth; `null` = driver default |
| `antenna` | string or null | Antenna port, e.g. `RX2`; `null` = driver default |
| `duplex` | enum or null | `half` or `full`; required when `tx_enabled: true` on any channel |

### `captures[].channels[]`

| Field | Type | Default | Notes |
|---|---|---|---|
| `id` | string | required | Unique across the whole config; used as ZMQ topic key |
| `rf_freq_hz` | int | required | Target station RF frequency in Hz |
| `bandwidth_hz` | int | required | Channel bandwidth after channelisation (e.g. `25000` for NBFM, `200000` for WFM) |
| `chain` | string | required | Registered modulation chain name (e.g. `nbfm`, `wfm`) |
| `chain_config` | object | `{}` | Chain-specific parameters — see below |
| `radio` | object | required | DIS Radio binding — see below |
| `tx_enabled` | bool | `false` | Enable TX on this channel — the Bridge will forward received Signal PDUs to ZMQ TX PUB |
| `tx_filter` | object or null | `null` | If set, only accept Signal PDUs from this specific entity/radio — see below |

#### `chain_config` for `nbfm`

| Field | Type | Default | Notes |
|---|---|---|---|
| `deviation_hz` | float | `5000` | Maximum FM deviation in Hz |
| `audio_lpf_hz` | float | `3400` | Post-demod audio low-pass cutoff |
| `squelch_db` | float | `-60` | Power squelch threshold (dBFS) — raise toward 0 to suppress noise floor |
| `squelch_ramp_ms` | float | `50` | Squelch attack/release time in ms |

#### `chain_config` for `wfm`

| Field | Type | Default | Notes |
|---|---|---|---|
| `squelch_db` | float | `-60` | Power squelch threshold (dBFS) |
| `squelch_ramp_ms` | float | `50` | Squelch attack/release time in ms |

WFM expects `bandwidth_hz: 200000` to cover the ±75 kHz FM deviation.

### `captures[].channels[].radio`

| Field | Type | Required | Notes |
|---|---|---|---|
| `radio_id` | int 1–65535 | yes | Unique within the parent Entity |
| `entity_id` | `{site, app, entity}` | yes | For standalone radios: the radio's own Entity ID. For attached radios: the parent entity's EID. |
| `attached` | bool | yes | Sets the DIS v7 PDU Status RAI flag |
| `relative_antenna_location` | [x, y, z] floats | — | Body-relative metres from entity origin; use `[0, 0, 0]` when `attached: false` |
| `antenna_location_ecef` | [x, y, z] float64 | yes | World ECEF coordinates in metres |
| `radio_entity_type` | object | yes | `{kind, domain, country, category, subcategory, specific, extra}` per SISO-REF-010. `kind: 7` = Radio. |
| `power_dbm` | float | `0.0` | Reported transmit power |
| `input_source` | int | `1` | DIS Input Source enum; `1 = Pilot` |

### `captures[].channels[].tx_filter`

When present, only Signal PDUs from the specified entity/radio trigger TX. When absent, the first entity that sends a Transmitter PDU with `Transmit State = On transmitting` acquires the TX lock for that channel frequency.

| Field | Type | Notes |
|---|---|---|
| `entity_id` | `{site, app, entity}` | Accepted entity |
| `radio_id` | int | Accepted radio ID on that entity |

---

## `rf_tx_authorization` (optional)

Restricts which RF frequencies the TX path may transmit on. If omitted, all TX-enabled channels are authorized.

```yaml
rf_tx_authorization:
  authorized_ranges:
    - from_hz: 144000000
      to_hz:   148000000
      note: "Amateur 2 m"
    - from_hz: 430000000
      to_hz:   440000000
      note: "Amateur 70 cm"
```

| Field | Type | Notes |
|---|---|---|
| `authorized_ranges[].from_hz` | int | Lower bound of authorized band (inclusive) |
| `authorized_ranges[].to_hz` | int | Upper bound of authorized band (inclusive) |
| `authorized_ranges[].note` | string | Human-readable label (optional) |

---

## Validation rules

Enforced at startup — the bridge will not start if any rule is violated:

- Each `channels[].id` is unique across the whole config.
- Each `(radio.entity_id, radio.radio_id)` pair is unique across the whole config.
- Every channel's `rf_freq_hz` lies within `[center_freq_hz ± sample_rate_hz/2 − bandwidth_hz/2]`.
- `dis.signal_pdu_ms` is a multiple of 20.

---

## TX path

The TX path routes DIS Signal PDUs → RF transmit. Enable it by:

1. Setting `bridge.zmq_tx_bind` (e.g. `tcp://127.0.0.1:5556`).
2. Setting `captures[].zmq_tx_connect` to the same endpoint.
3. Setting `tx_enabled: true` on each channel that should transmit.
4. Setting `captures[].sdr.duplex: half` if the SDR cannot receive and transmit simultaneously.

The Bridge listens on the DIS multicast group for Transmitter PDUs and Signal PDUs whose RF frequency matches a TX-enabled channel. When a Transmitter PDU with `Transmit State = On transmitting` is received, the Bridge acquires a **first-wins TX lock** for that channel. The lock is released when the Transmit State returns to `On but not transmitting`. Only the entity holding the lock can drive the transmitter; Signal PDUs from other entities are dropped.

See [`examples/config_nbfm_146950.yaml`](../examples/config_nbfm_146950.yaml) for a complete TX configuration example.

---

## TX loopback test

Verifies the TX path end-to-end without RF hardware using `examples/config_nbfm_146950.yaml` and the `tx_file_loopback` GRC flowgraph.

**Step 1 — start the bridge:**

```bash
gr-dis bridge --config examples/config_nbfm_146950.yaml
```

**Step 2 — run the TX loopback flowgraph** (records decoded IQ to `/tmp/nbfm_tx_146950.cf32`):

```bash
gnuradio-companion flowgraphs/tx_file_loopback.grc
```

**Step 3 — inject DIS PDUs** at 146.950 MHz NBFM. The bridge matches on frequency, acquires the TX lock, decodes the μ-law audio, and publishes PCM on `tcp://127.0.0.1:5556` where the flowgraph picks it up. Use `scripts/synthetic_tx.py` as a software source:

```bash
python scripts/synthetic_tx.py 10
```

**Step 4 — play back the recording through the RX chain:**

```bash
gr-dis run \
    --config examples/config_nbfm_146950.yaml \
    --source-file /tmp/nbfm_tx_146950.cf32
```

The file feeds through the NBFM RX chain and emits Signal PDUs on the DIS multicast group — completing the software-only round-trip.
