# gr-dis deployment

## Prerequisites

- Python ≥ 3.10 (for the bridge process)
- GNU Radio 3.10+ with `gr-soapy` and `gr-zeromq` (for the capture process, in whichever environment it runs)
- A `gr-dis` user: `sudo useradd -r -s /sbin/nologin gr-dis`
- Package installed: `sudo pip install -e /opt/gr-dis`

## Install

### Bridge (host)

```bash
sudo cp deploy/systemd/gr-dis-bridge.service /etc/systemd/system/
sudo mkdir -p /etc/gr-dis
sudo cp examples/config.example.yaml /etc/gr-dis/config.yaml
# edit /etc/gr-dis/config.yaml with real exercise values
sudo systemctl daemon-reload
sudo systemctl enable --now gr-dis-bridge
```

### Capture

The capture process requires GNU Radio and runs wherever it is installed — natively on the host, inside a container, or via another isolation mechanism.

> **Caveat:** Running the capture as a system-managed systemd service requires the `gr-dis` binary and GNU Radio to be accessible from the service's execution context. Treat the `gr-dis-capture@.service` template as a starting point. Options:
>
> 1. Run the capture under user systemd (`systemctl --user enable --now gr-dis-capture@cap_vhf`) when GNU Radio is installed in that user's environment.
> 2. Install GNU Radio directly on the host and point `ExecStart=` at the host `gr-dis` binary.
> 3. For a containerized GNU Radio setup, adapt `ExecStart=` to invoke the container runtime (e.g. `podman exec` or similar).
>
> Before enabling the unit, validate the underlying command runs as the target user: `sudo -u gr-dis gr-dis --help`.

```bash
sudo cp deploy/systemd/gr-dis-capture@.service /etc/systemd/system/
sudo systemctl daemon-reload
# Start one capture per entry in captures[] — use the id field:
sudo systemctl enable --now "gr-dis-capture@cap_vhf"
```

### Restart behaviour

`systemctl restart gr-dis-bridge` sends SIGTERM. The bridge emits an Off
Transmitter PDU for each radio, drains the ZMQ socket, and exits within ~2 s
(well inside `TimeoutStopSec=10s`). On restart, startup Transmitter PDUs are
emitted immediately.

## Logs

```bash
journalctl -u gr-dis-bridge -f
journalctl -u "gr-dis-capture@*" -f
```

Default output format is JSON (`log_format: json` in config). To switch to
plain text, set `log_format: text` in `bridge:`.

If you need file-based logs, add to the `[Service]` section:

```ini
StandardOutput=append:/var/log/gr-dis/bridge.log
StandardError=append:/var/log/gr-dis/bridge.log
```

Then install the logrotate config:

```bash
sudo cp deploy/logrotate/gr-dis /etc/logrotate.d/gr-dis
sudo logrotate -d /etc/logrotate.d/gr-dis   # dry-run test
```

## Health check

```bash
curl -sf http://127.0.0.1:9180/healthz && echo healthy || echo degraded
```

Returns `200 OK` when all per-channel heartbeats are alive. Returns `503`
with a list of dead channels if any heartbeat has crashed beyond its retry
budget (1 initial attempt + 3 retries with linear back-off of 1 s, 2 s, 3 s
between retries = 4 total attempts before the channel is marked dead).
