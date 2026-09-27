#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
probe_dir=$(mktemp -d)
trap 'rm -rf "$probe_dir"' EXIT
swiftc -parse-as-library -framework AppKit -framework WebKit \
  mac/Sources/Duckterm/DashboardNavigation.swift mac/Tests/artifact-preview.swift \
  -o "$probe_dir/artifact-preview"
"$probe_dir/artifact-preview"
