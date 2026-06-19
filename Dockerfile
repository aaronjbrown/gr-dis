# Bridge-only image (no GNU Radio).
# The capture process must run in the gr-dis Fedora toolbox or on a GR-capable host.
#
# Build:
#   docker build -t gr-dis-bridge .
#
# Run:
#   docker run --rm \
#     -v /path/to/config.yaml:/config/config.yaml:ro \
#     --network host \
#     gr-dis-bridge

FROM python:3.13-slim

WORKDIR /app
COPY . .
RUN pip install --no-cache-dir .

# Prometheus metrics / healthz (informational — --network host bypasses port mapping anyway)
EXPOSE 9180

# Config must be mounted at /config/config.yaml
VOLUME ["/config"]

ENTRYPOINT ["gr-dis", "bridge"]
CMD ["--config", "/config/config.yaml"]
