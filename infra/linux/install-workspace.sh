#!/bin/bash
# Run as administrator with a source tree containing the built dashboard.
set -euo pipefail
SOURCE_DIR="$(cd "$(dirname "$0")/../.." && pwd)"
test "$(id -u)" = 0 || { echo 'Run as root'; exit 1; }
test -f "$SOURCE_DIR/src/duckterm/dashboard/index.html"
id duckterm >/dev/null
install -d -m 0755 /opt/duckterm
python3 -m venv /opt/duckterm/venv
/opt/duckterm/venv/bin/pip install --no-deps "$SOURCE_DIR"
install -d -o duckterm -g duckterm -m 0700 /home/duckterm/projects /home/duckterm/.duckterm
install -m 0644 "$SOURCE_DIR/infra/linux/duckterm.service" /etc/systemd/system/duckterm.service
systemctl daemon-reload
systemctl enable duckterm.service
systemctl restart duckterm.service
