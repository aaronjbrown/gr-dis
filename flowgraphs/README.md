# gr-dis Dev Flowgraph

`capture_dev.grc` is a dual-channel WBFM developer flowgraph that publishes audio to a running `gr-dis bridge` over ZMQ. It mirrors what `gr-dis run` does for the capture side, but exposes sliders so you can retune and adjust squelch live in GNU Radio Companion.

## Prerequisites

1. **GNU Radio environment with gr-dis installed** — `gr_dis` must be editable-installed in the environment where GNU Radio is available AND the GRC block definitions must be installed:
   ```bash
   make install
   ```
   This runs `pip install -e ".[dev]"` and copies `grc/*.block.yml` to
   `~/.local/share/gnuradio/grc/blocks/`.  See the top-level `README.md`
   Installation section for details.

2. **Bridge running** — in a separate terminal (the bridge has no GNU Radio dependency and runs wherever Python is available):
   ```bash
   gr-dis bridge --config examples/config_wfm.yaml
   ```
   The bridge binds `tcp://127.0.0.1:5555` by default; the flowgraph connects to it.
   The config must contain channels with IDs matching `channel_id_1` and `channel_id_2`
   (`fm_ch1` and `fm_ch2` by default — both present in `config_wfm.yaml`).

## Opening in GNU Radio Companion

```bash
gnuradio-companion flowgraphs/capture_dev.grc
```

Hit **Run** (F6). The flowgraph starts streaming immediately; audio is published to the bridge as int16 PCM frames.

## Variables to edit before running

| Variable | Default | What it means |
|---|---|---|
| `center_freq` | `107.3e6` | SDR LO — set to the midpoint of your two target stations |
| `samp_rate` | `2400000` | SDR sample rate — must cover both station offsets within ±(samp_rate/2) |
| `audio_rate` | `48000` | Audio output rate — must be supported by your audio hardware |
| `zmq_endpoint` | `tcp://127.0.0.1:5555` | Bridge ZMQ bind address (`bridge.zmq_bind` in config) |
| `channel_id_1` | `fm_ch1` | Must match a `channels[].id` in your bridge config |
| `channel_id_2` | `fm_ch2` | Must match a `channels[].id` in your bridge config |

For SDR hardware selection, double-click the **Soapy RTLSDR Source** block and set `dev_args` if needed (e.g. `serial=00000001` to select a specific dongle). To use other SDR hardware, replace the block with the appropriate Soapy source variant.

## Live sliders

| Slider | Effect |
|---|---|
| **RF 1 / RF 2 Frequency** | Retunes the per-channel freq_xlating filter live. Also sends a `meta` ZMQ frame, which causes the bridge to emit a fresh Transmitter PDU with the updated `rf_freq_hz`. Step = 100 kHz; range = center_freq ± samp_rate/2. |
| **Squelch (dBFS)** | Shared squelch threshold for both channels. Open (lower) if a station is being muted; raise to suppress noise floor. |
| **Gain (dB)** | SDR front-end gain. Aim for a signal just below 0 dBFS on a spectrum display. |

## Signal chain (per channel)

```
Soapy RTLSDR Source (fc32, 2.4 MHz)
  → freq_xlating_fir_filter_ccf   # shift station to DC; 100 kHz LPF; decim=1
  → analog_pwr_squelch_cc         # gate on carrier power
  → analog_wfm_rcv                # WBFM demod (±75 kHz dev); decimate to audio_rate
  → blocks_multiply_const_vxx     # float → int16 scale (×32767)
  → blocks_float_to_short         # cast to int16
  → ZMQ Audio Sink (gr-dis)       # gr_dis wire-protocol publisher
```

The SDR is tuned to `center_freq`; both channels share one source. The xlating filter shifts each station to DC without decimating (`decim=1`), keeping the full 2.4 MHz bandwidth for `wfm_rcv` to demodulate correctly across the ±75 kHz FM deviation. `wfm_rcv` performs the decimation internally via `audio_decimation = samp_rate / audio_rate`.

Each ZMQ publisher sends three topic types on `tcp://127.0.0.1:5555`:
- `audio.<channel_id>` — 20 ms int16 PCM frames at `audio_rate`
- `meta.<channel_id>` — channel metadata heartbeat (every 5 s, and on retune)
- `event.<channel_id>` — `squelch_open` / `squelch_close` transitions

## Testing DIS output

With the bridge running, verify PDUs are flowing:

**Terminal 1 — bridge** (host, no GNU Radio needed):
```bash
gr-dis bridge --config examples/config_wfm.yaml
```

**Terminal 2 — multicast listener:**
```bash
python3 scripts/e2e-listener.py --duration 30
```

**Terminal 3 — flowgraph** (in GNU Radio environment):
```bash
gnuradio-companion flowgraphs/capture_dev.grc
# Hit Run (F6), tune both sliders to live FM stations
```

Expected listener output:
- 2 startup Transmitter PDUs at t ≈ 0 (one per radio, `tx_state=1 OnNotTx`)
- Signal PDUs on both channels while stations are broadcasting (`tx_state=2 OnTx`)
- Transmitter heartbeats every 5 s on both radios
- `tx_state` flips back to `1 OnNotTx` if squelch closes (e.g. station goes silent)

For tshark PDU inspection:
```bash
tshark -i any -c 100 -O dis 'udp port 3000 and dst host 239.1.2.3'
```

## Notes

- **Two-station coverage**: `center_freq` must be set so both `rf_freq_1` and `rf_freq_2` fall within `center_freq ± (samp_rate/2)`. At 2.4 MHz sample rate, the Nyquist window is ±1.2 MHz.
- **Adding channels**: duplicate one xlating filter → squelch → wfm_rcv → scale → convert → EPB chain and add a `channel_id_N` / `rf_freq_N` variable pair.
- **File-source testing**: for offline testing without hardware, enable `blocks_file_source_0` and `blocks_throttle2_0`, disable `soapy_rtlsdr_source_0`. The file source reads the synthetic NBFM fixture — regenerate with `scripts/synth-nbfm-fixture.py` if missing.
