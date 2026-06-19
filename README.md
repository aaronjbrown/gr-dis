# gr-dis

`gr-dis` bridges a GNU Radio software-defined radio and a DIS (IEEE 1278.1-2012 v7) exercise network in both directions:

- **RX**: tunes a SoapySDR receiver to one or more RF channels, demodulates audio via configurable modulation chains, and emits Transmitter and Signal PDUs to a DIS multicast group.
- **TX**: listens for Signal PDUs on the DIS multicast group, decodes the μ-law audio, and drives a SoapySDR transmitter to re-broadcast over RF.

```
RX  RF ──► [Capture: SoapySDR → rx_chain → ZMQ PUB]
                                      │
                                 ZMQ frames
                                      ▼
           [Bridge: ZMQ SUB → μ-law encode → PDU builder] ──► DIS multicast

TX  DIS multicast ──► [Bridge: PDU parser → μ-law decode → ZMQ PUB]
                                                                │
                                                           ZMQ frames
                                                                ▼
                       RF ◄── [Capture: tx_chain ◄── ZMQ SUB ◄─┘
```

The Bridge and Capture are separate processes. The **Bridge is pure Python** and has no GNU Radio dependency; the **Capture side requires GNU Radio** and runs in the same environment as the SDR hardware.

## Prerequisites

| Component | Purpose |
|---|---|
| Python ≥ 3.10 | Bridge, CLI, config validation |
| GNU Radio 3.10 with `gr-soapy` and `gr-zeromq` | Capture process (RX/TX chains) |
| SoapySDR + hardware driver | Live RF capture/transmit (`rtlsdr`, `hackrf`, `uhd`, `lime`, …) |
| `tshark` | Optional: PDU inspection and golden-PDU validation |

## Installation

### Python package (bridge + CLI)

```bash
pip install -e .           # bridge + CLI
pip install -e ".[dev]"    # + pytest, ruff, mypy
```

Smoke-test:

```bash
gr-dis validate --config examples/config.example.yaml
```

### GRC block definitions (Capture side only)

The `grc/` directory contains GNU Radio Companion block definitions for the two
custom GR blocks (`ZMQ Audio Sink` and `ZMQ TX Source`).  Install them in the
environment where GNU Radio is installed:

```bash
make install-grc
```

This copies `grc/*.block.yml` to `~/.local/share/gnuradio/grc/blocks/` — the
user-level path that GRC searches on startup.  After installation the blocks
appear in GRC's block palette under the **`[gr-dis]`** category.

To use a different install path (e.g. system-wide):

```bash
make install-grc GRC_BLOCKS_DIR=$(gnuradio-config-info --prefix)/share/gnuradio/grc/blocks
```

To remove:

```bash
make uninstall-grc
```

> **Note:** the Python package (`pip install -e .`) must also be installed
> in the same environment as GNU Radio for the GRC blocks to import correctly
> at flowgraph runtime.  Run `make install` to do both steps together.

## Usage

### Validate a config

```bash
gr-dis validate --config examples/config.example.yaml
```

Exits 0 on success; prints the failing field path and reason on error.

### Bridge only (no GNU Radio required)

Bind the ZMQ SUB socket and wait for Captures to connect:

```bash
gr-dis bridge --config examples/config.example.yaml
```

The bridge:
- Binds at `bridge.zmq_bind` (default `tcp://127.0.0.1:5555`)
- Emits a startup Transmitter PDU per configured radio
- Sends Transmitter heartbeats at `dis.heartbeat_interval_seconds`
- μ-law encodes incoming audio frames and emits Signal PDUs
- Flips Transmit State on `squelch_open` / `squelch_close` events

Stop with `Ctrl-C` or `SIGTERM` — the bridge emits an Off Transmitter PDU per radio before exiting.

### Run Capture + Bridge together

`gr-dis run` starts the Bridge and one Capture as a supervised process group. Use `--source-file` for offline testing with a recorded IQ file (no SDR hardware needed):

```bash
gr-dis run --config examples/config.example.yaml \
           --source-file tests/fixtures/recorded_iq/nbfm_voice.cf32
```

Generate the synthetic NBFM fixture if it is missing (~73 MiB, not committed):

```bash
python scripts/synth-nbfm-fixture.py
```

Live SDR:

```bash
gr-dis run --config examples/config.example.yaml
```

| Flag | Effect |
|---|---|
| `--config PATH` | YAML config file (required) |
| `--capture ID` | Select a specific entry from `captures[]` (default: first) |
| `--source-file PATH` | Play back a complex-float-32 IQ file instead of opening the SDR |
| `--no-bridge` | Do not start the Bridge in this process (useful when running Bridge separately) |

### TX path: DIS → RF

The TX path receives Signal PDUs from the DIS exercise network and re-broadcasts the decoded audio over RF. Enable it by adding `bridge.zmq_tx_bind`, setting `zmq_tx_connect` on the Capture, and marking each channel with `tx_enabled: true`.

See [`examples/config_nbfm_146950.yaml`](examples/config_nbfm_146950.yaml) for a worked TX example and [`docs/configuration.md`](docs/configuration.md#tx-path) for the full field reference.

### Observe DIS output

```bash
tshark -i any -O dis -V "dst host 239.1.2.3 and udp port 3000"
```

Scrape Prometheus metrics:

```bash
curl http://127.0.0.1:9180/metrics | grep '^gr_dis_'
```

## Configuration

Three top-level YAML keys:

```yaml
dis:       { ... }   # DIS network and exercise binding
bridge:    { ... }   # Bridge process settings
captures:  [ ... ]   # One entry per SDR session / GR flowgraph
```

Copy an example config and replace the `<<TODO>>` fields with values from your exercise operator:

```bash
cp examples/config.example.yaml config.yaml
gr-dis validate --config config.yaml
```

Full schema reference: [`docs/configuration.md`](docs/configuration.md).

Cross-field validation rules are enforced at load time: see [Configuration reference → Validation rules](docs/configuration.md#validation-rules).

## Observability

The Bridge exposes a Prometheus metrics endpoint at `http://127.0.0.1:9180/metrics` (configurable via `bridge.metrics_bind`) and a health check at `/healthz`.

| Metric | Labels | Description |
|---|---|---|
| `gr_dis_signal_pdus_sent_total` | `channel` | Signal PDUs sent to DIS |
| `gr_dis_transmitter_pdus_sent_total` | `radio` | Transmitter PDUs sent to DIS |
| `gr_dis_audio_frames_received_total` | `channel` | ZMQ audio frames received (RX) |
| `gr_dis_audio_frames_dropped_total` | `channel`, `reason` | Dropped before PDU emission |
| `gr_dis_zmq_hwm_drops_total` | `channel` | Estimated ZMQ HWM drops |
| `gr_dis_e2e_latency_seconds` | — | RF capture → Signal PDU on wire (histogram) |
| `gr_dis_rx_transmitter_pdus_received_total` | `channel` | Transmitter PDUs received from DIS (TX path) |
| `gr_dis_rx_signal_pdus_received_total` | `channel` | Signal PDUs received from DIS (TX path) |
| `gr_dis_tx_audio_frames_published_total` | `channel` | PCM frames published to ZMQ TX (TX path) |
| `gr_dis_tx_audio_frames_dropped_total` | `channel`, `reason` | Dropped in TX path |

```bash
curl -sf http://127.0.0.1:9180/healthz && echo healthy || echo degraded
```

`/healthz` returns 200 when all channel heartbeats are alive; 503 with a list of dead channels otherwise.

Logs default to structured JSON on stdout. Switch to plain text with `log_format: text` in `bridge:`.

## Testing

```bash
ruff check .                                              # lint
mypy src/                                                 # type check
pytest -q                                                 # all tests
pytest tests/unit/ -q                                     # unit tests only (no network, no ZMQ, no GR)
pytest tests/integration/test_bridge_synthetic.py -v      # bridge E2E (~3 s)
python scripts/golden-pdu-validate.py                     # validate PDU bytes against tshark
pytest -q -m "not slow"                                   # skip the 32-channel stress test
```

## Project layout

```
src/gr_dis/
├── cli.py                    # gr-dis {validate,bridge,run}
├── metrics.py                # Prometheus exporter + /healthz
├── engine/                   # GR-side (runs in the Capture process)
│   ├── config.py             # Pydantic v2 config models
│   ├── capture.py            # GR top-block builder
│   ├── zmq_sink.py           # gr.sync_block: PCM → ZMQ PUB (RX path)
│   ├── zmq_source.py         # gr.sync_block: ZMQ SUB → PCM (TX path)
│   ├── rx_chains/            # RX demodulation chains
│   │   ├── base.py           # ModulationChain ABC
│   │   ├── __init__.py       # registry (@register, get_chain)
│   │   ├── nbfm.py           # NBFM chain (±5 kHz, 25 kHz channel)
│   │   └── wfm.py            # WFM broadcast chain (±75 kHz, 200 kHz channel)
│   └── tx_chains/            # TX modulation chains
│       ├── base.py           # TxModulationChain ABC
│       ├── __init__.py       # registry (@register_tx, get_tx_chain)
│       └── nbfm_tx.py        # NBFM TX chain
└── bridge/                   # Pure Python; no GNU Radio dependency
    ├── main.py               # async entrypoint
    ├── subscriber.py         # ZMQ SUB consumer (RX path)
    ├── dis_listener.py       # DIS multicast listener (TX path)
    ├── radio_state.py        # per-radio FSM + Transmitter PDU heartbeats
    ├── tx_channel.py         # TX lock state per channel
    ├── tx_publisher.py       # ZMQ PUB for decoded TX audio
    ├── multicast.py          # UDP socket factory
    ├── encoder_ulaw.py       # G.711 μ-law encode/decode
    └── pdu/                  # Pure byte builders; no I/O
        ├── header.py         # DIS PDU header (12 bytes)
        ├── transmitter.py    # Transmitter PDU (type 25)
        ├── signal.py         # Signal PDU (type 26)
        ├── parser.py         # PDU parser (TX path)
        ├── emission.py       # Emission designator helper
        ├── timestamp.py      # DIS timestamp encoding
        └── enums.py          # PDU type and encoding constants

examples/                     # Example configs — copy and fill in <<TODO>> values
flowgraphs/                   # GRC developer flowgraphs (see flowgraphs/README.md)
deploy/                       # systemd units, logrotate config (see deploy/README.md)
scripts/                      # Development and test utilities
tests/
├── unit/                     # No I/O; imports only bridge.pdu.*, encoder_ulaw, config
└── integration/              # Starts a real bridge + ZMQ, asserts PDU output
```

Key boundaries:
- `engine/` has no knowledge of DIS — only the ZMQ wire protocol.
- `bridge/pdu/` has no knowledge of GR, ZMQ, or config — pure byte builders.
- `bridge/main.py` is the only place that wires the two together.

## Contributing

- `ruff check .` and `mypy src/` must both be clean before committing.
- New modules under `engine/` that import `gnuradio.*` must defer those imports to method bodies (see the existing chains for the pattern).
- Adding a new modulation chain: see [`src/gr_dis/engine/README.md`](src/gr_dis/engine/README.md).
- DIS format changes require regenerating the golden-PDU fixtures: `python scripts/golden-pdu-validate.py`.

## License

Copyright (C) 2026 gr-dis contributors.

Licensed under the [GNU Affero General Public License v3.0](LICENSE) (AGPL-3.0-only). You must release source for any modified version you run as a network service.
