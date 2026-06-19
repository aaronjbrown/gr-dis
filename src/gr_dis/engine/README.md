# Modulation Chains

Both RX (demodulation) and TX (modulation) chains are self-registering plugins. Adding a new chain requires only a new Python file — no edits to the CLI, Bridge, registry, or any other chain.

---

## How the registry works

Chains register themselves at import time via a class decorator:

- **RX chains**: `@register("name")` from `gr_dis.engine.rx_chains`
- **TX chains**: `@register_tx("name")` from `gr_dis.engine.tx_chains`

On first call to `get_chain("name")` or `get_tx_chain("name")`, the registry scans all `.py` files in the respective package directory (excluding `__init__.py` and `base.py`) and imports them, triggering registration as a side effect. Any `.py` file dropped into `rx_chains/` or `tx_chains/` is picked up automatically.

The decorator name is the value written in the YAML config under `chain:`.

---

## Adding an RX chain

An RX chain takes complex baseband IQ at the SDR sample rate and produces mono int16 PCM at 8 kHz.

**1. Create the file** — `src/gr_dis/engine/rx_chains/mychain.py`:

```python
from __future__ import annotations
from typing import TYPE_CHECKING, Any

from gr_dis.engine.rx_chains import register
from gr_dis.engine.rx_chains.base import ModulationChain

if TYPE_CHECKING:          # see "GNU Radio import guard" below
    from gnuradio import gr


@register("mychain")
class MyChain(ModulationChain):
    def build(
        self,
        input_sample_rate_hz: float,
        channel_bandwidth_hz: float,
        rf_offset_hz: float,
        chain_config: dict[str, Any],
    ) -> gr.hier_block2:
        from gnuradio import gr, blocks, analog, filter  # deferred import

        hb = gr.hier_block2(
            "MyChain",
            gr.io_signature(1, 1, gr.sizeof_gr_complex),   # one complex input
            gr.io_signature(1, 1, gr.sizeof_short),         # one int16 output
        )

        # Build and connect GR blocks here.
        # The output must be mono PCM at exactly 8000 Hz.

        return hb
```

**2. Use it in config**:

```yaml
channels:
  - chain: mychain
    bandwidth_hz: 25000
    chain_config:
      my_param: 42
```

No other files need editing.

### `build` parameters

| Parameter | Type | Notes |
|---|---|---|
| `input_sample_rate_hz` | float | SDR complex sample rate (from `captures[].sdr.sample_rate_hz`) |
| `channel_bandwidth_hz` | float | From `channels[].bandwidth_hz` in config |
| `rf_offset_hz` | float | `rf_freq_hz − center_freq_hz` — the chain must frequency-translate by this amount to shift the target station to DC |
| `chain_config` | dict | Passed through verbatim from `chain_config:` in the YAML |

Return a `gr.hier_block2` with **one complex input** (IQ at `input_sample_rate_hz`) and **one int16 output** (mono PCM at 8000 Hz).

---

## Adding a TX chain

A TX chain takes mono int16 PCM at 8 kHz and produces complex baseband IQ at the SDR sample rate, centred at the channel's RF offset.

TX chains have two additional required class attributes that the Bridge uses to match incoming DIS PDUs to the correct chain:

- `dis_mod_major` — DIS Modulation Major (see `bridge/pdu/enums.py` for `MOD_MAJOR_*` constants)
- `dis_mod_detail` — DIS Modulation Detail (see `MOD_DETAIL_*` constants)

**1. Create the file** — `src/gr_dis/engine/tx_chains/mychain_tx.py`:

```python
from __future__ import annotations
from typing import TYPE_CHECKING, Any

from gr_dis.bridge.pdu.enums import MOD_DETAIL_FM_ANGLE, MOD_MAJOR_ANGLE
from gr_dis.engine.tx_chains import register_tx
from gr_dis.engine.tx_chains.base import TxModulationChain

if TYPE_CHECKING:          # see "GNU Radio import guard" below
    from gnuradio import gr


@register_tx("mychain")
class MyTxChain(TxModulationChain):
    dis_mod_major = MOD_MAJOR_ANGLE
    dis_mod_detail = MOD_DETAIL_FM_ANGLE

    def build_tx(
        self,
        output_sample_rate_hz: float,
        rf_offset_hz: float,
        channel_bandwidth_hz: float,
        chain_config: dict[str, Any],
    ) -> gr.hier_block2:
        from gnuradio import gr, blocks, analog, filter  # deferred import

        hb = gr.hier_block2(
            "MyTxChain",
            gr.io_signature(1, 1, gr.sizeof_short),         # one int16 input
            gr.io_signature(1, 1, gr.sizeof_gr_complex),    # one complex output
        )

        # Build and connect GR blocks here.
        # The output must be complex IQ at output_sample_rate_hz,
        # frequency-shifted to rf_offset_hz.

        return hb
```

**2. Enable TX in config**:

```yaml
bridge:
  zmq_tx_bind: tcp://127.0.0.1:5556

captures:
  - zmq_tx_connect: tcp://127.0.0.1:5556
    sdr:
      duplex: half
    channels:
      - chain: mychain
        tx_enabled: true
```

### `build_tx` parameters

| Parameter | Type | Notes |
|---|---|---|
| `output_sample_rate_hz` | float | SDR complex sample rate (from `captures[].sdr.sample_rate_hz`) |
| `rf_offset_hz` | float | `rf_freq_hz − center_freq_hz` — the chain must up-convert to this offset |
| `channel_bandwidth_hz` | float | From `channels[].bandwidth_hz` in config |
| `chain_config` | dict | Passed through verbatim from `chain_config:` in the YAML |

Return a `gr.hier_block2` with **one int16 input** (mono PCM at 8000 Hz) and **one complex output** (IQ at `output_sample_rate_hz`, centred at `rf_offset_hz`).

---

## GNU Radio import guard

All GNU Radio imports inside chain files must be deferred to the `build()` / `build_tx()` method body. Do **not** import `gnuradio` at module level.

The Bridge process has no GNU Radio dependency. Top-level `gnuradio` imports would make the Bridge fail to start in environments where GNU Radio is not installed. Deferring to method bodies keeps every module importable without GNU Radio, which also keeps the unit-test suite runnable without a GNU Radio installation.

Pattern (used in every built-in chain):

```python
if TYPE_CHECKING:             # type-checker sees the type; runtime skips the import
    from gnuradio import gr

def build(self, ...) -> gr.hier_block2:
    from gnuradio import gr, blocks, filter, analog   # imported here, not at module level
    ...
```

---

## Python-wrapped block lifetime

`gr.top_block.connect()` extends the lifetime of the **C++** block (via `shared_ptr`) but does **not** root the **Python wrapper** that owns it. For any `gr.sync_block` subclass defined in Python, the wrapper must be held by something outside the construction loop's local scope — otherwise it is GC'd while the C++ block still holds a pointer to it, manifesting as a SIGSEGV in `PyObject_GetAttrString` from a GR worker thread during `tb.start()`.

`engine/capture.py` solves this by appending every Python-wrapped block to `tb._py_blocks`:

```python
tb._py_blocks = []          # holds Python wrappers so GC doesn't collect them
...
tb._py_blocks.append(sink)  # sink is a gr.sync_block subclass
tb.connect(src, sink)
```

GRC-generated code achieves the same effect implicitly because each block is assigned to `self.block_name` on the top_block subclass, which roots the Python object for the lifetime of the flowgraph.

Any new Python-wrapped block added to `engine/capture.py` **must** be appended to `tb._py_blocks` before calling `tb.connect()`.

---

## Built-in chains

| Name | Direction | Modulation | Typical `bandwidth_hz` |
|---|---|---|---|
| `nbfm` | RX | Narrow-band FM (±5 kHz default) | `25000` |
| `wfm` | RX | Wide-band FM broadcast (±75 kHz) | `200000` |
| `nbfm` | TX | Narrow-band FM (±5 kHz default) | `25000` |
