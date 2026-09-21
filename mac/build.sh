#!/usr/bin/env bash
# Build RubberTerm.app — the desktop shell around the local dashboard.
#
# Compiles the Swift sources directly (not via SwiftPM) into a .app bundle and
# ad-hoc signs it so it runs on this machine. Requires a working Swift toolchain
# with the macOS SDK — full Xcode is recommended; the standalone CommandLineTools
# 16.4 SDK has a broken module map (duplicate SwiftBridging) that fails to import
# AppKit. If you hit that, install Xcode and:
#   sudo xcode-select -s /Applications/Xcode.app/Contents/Developer
#
# Usage:
#   ./build.sh --test --run  # build and open RubberTerm Test
#   ./build.sh              # build the production RubberTerm bundle
set -euo pipefail
cd "$(dirname "$0")"

TEST_BUILD=0
RUN_APP=0
for arg in "$@"; do
  case "$arg" in
    --test) TEST_BUILD=1 ;;
    --run) RUN_APP=1 ;;
    *) echo "Usage: $0 [--test] [--run]" >&2; exit 2 ;;
  esac
done
APP_NAME="RubberTerm"
BUNDLE_ID="com.rubberduckhq.rubberterm"
ICON_NAME="AppIcon"
if [[ "$TEST_BUILD" == 1 ]]; then
  APP_NAME="RubberTerm Test"
  BUNDLE_ID="com.rubberduckhq.rubberterm.test"
  ICON_NAME="AppIconTest"
fi
APP="build/$APP_NAME.app"
CONTENTS="$APP/Contents"
MACOS="$CONTENTS/MacOS"

if [[ "$TEST_BUILD" == 1 ]]; then
  echo "==> building Test dashboard"
  (cd ../web && npm run build --silent)
fi

echo "==> compiling"
rm -rf "$APP"
mkdir -p "$MACOS" "$CONTENTS/Resources"
swiftc -O \
  -framework AppKit -framework WebKit -framework UserNotifications -framework Foundation \
  -o "$MACOS/RubberTerm" \
  Sources/Duckterm/*.swift

echo "==> bundling app icon"
# Regenerate the icns from the dashboard duck so the app icon never drifts from
# the brand mark. Skips if node/playwright isn't available (uses the checked-in
# icns as-is). Needs web/node_modules (run `npm ci` in web/ once).
if command -v node >/dev/null 2>&1 && [ -d ../web/node_modules/playwright ]; then
  if [[ "$TEST_BUILD" == 1 ]]; then
    node make-icon.mjs --test
  else
    node make-icon.mjs || echo "   (icon regen failed; using existing AppIcon.icns)"
  fi
fi
cp "Resources/$ICON_NAME.icns" "$CONTENTS/Resources/AppIcon.icns"

echo "==> writing Info.plist"
# Bundle version tracks the Python package (single source of truth) so the
# app's About/Get Info never claims an older RubberTerm than the one it runs.
VERSION="$("${PYTHON:-../.venv/bin/python}" -c 'import duckterm; print(duckterm.__version__)')"
cat > "$CONTENTS/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key><string>${APP_NAME}</string>
  <key>CFBundleDisplayName</key><string>${APP_NAME}</string>
  <key>CFBundleIdentifier</key><string>${BUNDLE_ID}</string>
  <key>CFBundleVersion</key><string>${VERSION}</string>
  <key>CFBundleShortVersionString</key><string>${VERSION}</string>
  <key>CFBundleExecutable</key><string>RubberTerm</string>
  <key>CFBundleIconFile</key><string>AppIcon</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>LSMinimumSystemVersion</key><string>13.0</string>
  <key>NSHumanReadableCopyright</key><string>RubberDuckHQ</string>
</dict>
</plist>
PLIST

if [[ "$TEST_BUILD" == 1 ]]; then
  # Snapshot this worktree's backend; never fall back to the installed production CLI.
  "${PYTHON:-../.venv/bin/python}" - "$CONTENTS" <<'PYBUILD'
import os, plistlib, shutil, sys
from pathlib import Path
contents = Path(sys.argv[1])
shutil.copytree('../src/duckterm', contents / 'Resources/backend/duckterm',
                ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
dashboard = contents / 'Resources/backend/duckterm/dashboard'
if dashboard.exists():
    shutil.rmtree(dashboard)
shutil.copytree('../web/dist', dashboard)
with (contents / 'Info.plist').open('rb') as f:
    info = plistlib.load(f)
info['DucktermTestBuild'] = True
if remote_host := os.environ.get('DUCKTERM_TEST_REMOTE_HOST'):
    info['DucktermTestRemoteHost'] = remote_host
info['DucktermTestPython'] = os.path.abspath(sys.executable)
# Compute the existing instance scheme without inheriting per-facet overrides.
for key in ('DUCKTERM_PORT', 'DUCKTERM_URL', 'DUCKTERM_HOME', 'DUCKTERM_TMUX_SOCKET'):
    os.environ.pop(key, None)
os.environ['DUCKTERM_INSTANCE'] = 'test'
from duckterm.helpers import instance
info['DucktermTestPort'] = instance.port()
with (contents / 'Info.plist').open('wb') as f:
    plistlib.dump(info, f)
PYBUILD
fi

echo "==> ad-hoc signing (runs locally; not notarized for distribution)"
codesign --force --deep --sign - "$APP"

echo "==> done: $APP"
if [[ "$RUN_APP" == 1 ]]; then
  open "$APP"
fi
