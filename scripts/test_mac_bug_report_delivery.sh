#!/usr/bin/env bash
# Injected transport only: never submits a report or uses real credentials.
set -euo pipefail
cd "$(dirname "$0")/.."
REPORT_DELIVERY_TEST_DIR="$(mktemp -d)"
trap 'rm -rf "$REPORT_DELIVERY_TEST_DIR"' EXIT
swiftc mac/Sources/Duckterm/BugReportDelivery.swift mac/Tests/BugReportDeliveryTests.swift \
  -o "$REPORT_DELIVERY_TEST_DIR/report-delivery-tests"
"$REPORT_DELIVERY_TEST_DIR/report-delivery-tests"
