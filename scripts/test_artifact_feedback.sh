#!/usr/bin/env bash
# First run the artifact-feedback browser test to emit its actual preview fixture.
set -euo pipefail
cd "$(dirname "$0")/.."
probe_dir=$(mktemp -d)
trap 'rm -rf "$probe_dir"' EXIT
swiftc -parse-as-library -framework AppKit -framework WebKit mac/Tests/artifact-feedback.swift -o "$probe_dir/artifact-feedback"
"$probe_dir/artifact-feedback" "${1:-/tmp/duckterm-feedback-native.json}"
