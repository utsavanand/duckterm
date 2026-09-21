#!/bin/bash
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
apt-get update -q
apt-get install -y -q ca-certificates curl git tmux python3 python3-venv python3-pip pipx build-essential jq
if ! id duckterm >/dev/null 2>&1; then
  useradd --create-home --shell /bin/bash duckterm
fi
chmod 0700 /home/duckterm
install -d -m 0700 -o duckterm -g duckterm /home/duckterm/projects /home/duckterm/.duckterm
if ! id duckterm-broker >/dev/null 2>&1; then
  useradd --system --create-home --home-dir /var/lib/duckterm-broker --shell /usr/sbin/nologin duckterm-broker
fi
chmod 0700 /var/lib/duckterm-broker
install -d -m 0700 -o root -g root /etc/duckterm-broker
install -d -m 0755 /var/lib/duckterm-provisioning
date --utc --iso-8601=seconds > /var/lib/duckterm-provisioning/bootstrap-complete
