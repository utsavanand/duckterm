#!/usr/bin/env bash
# Build DuckTerm.app — the desktop shell around the local dashboard.
#
# Compiles the Swift sources directly (not via SwiftPM) into a .app bundle and
# ad-hoc signs it so it runs on this machine. Requires a working Swift toolchain
# with the macOS SDK — full Xcode is recommended; the standalone CommandLineTools
# 16.4 SDK has a broken module map (duplicate SwiftBridging) that fails to import
# AppKit. If you hit that, install Xcode and:
#   sudo xcode-select -s /Applications/Xcode.app/Contents/Developer
#
# Usage:
#   ./build.sh --test --run  # build and open DuckTerm Test
#   ./build.sh              # build the production DuckTerm bundle
set -euo pipefail
cd "$(dirname "$0")"

TEST_BUILD=0
RUN_APP=0
EDITION=full
# Both the binary's deployment target and Info.plist read this. Without an
# explicit -target, swiftc stamps the BUILD machine's OS as the minimum, and
# the app refuses to launch on older macOS despite what Info.plist says.
MIN_MACOS=13.0
for arg in "$@"; do
  case "$arg" in
    --test) TEST_BUILD=1 ;;
    --run) RUN_APP=1 ;;
    --edition=lite|--edition=code|--edition=full) EDITION="${arg#--edition=}" ;;
    *) echo "Usage: $0 [--test] [--run] [--edition=lite|code|full]" >&2; exit 2 ;;
  esac
done
APP_NAME="DuckTerm"
BUNDLE_ID="com.rubberduckhq.rubberterm"
ICON_NAME="AppIcon"
case "$EDITION" in
  lite) APP_NAME="DuckTerm Lite" ;;
  code) APP_NAME="DuckTerm Code" ;;
esac
if [[ "$TEST_BUILD" == 1 ]]; then
  APP_NAME="$APP_NAME Test"
  BUNDLE_ID="com.rubberduckhq.rubberterm.test"
  ICON_NAME="AppIconTest"
fi
APP="build/$APP_NAME.app"
CONTENTS="$APP/Contents"
MACOS="$CONTENTS/MacOS"

echo "==> building dashboard"
(cd ../web && npm run build --silent)

echo "==> compiling"
rm -rf "$APP"
mkdir -p "$MACOS" "$CONTENTS/Resources"
swiftc -O -target "arm64-apple-macos${MIN_MACOS}" \
  -framework AppKit -framework WebKit -framework UserNotifications -framework Foundation \
  -o "$MACOS/DuckTerm" \
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
# app's About/Get Info never claims an older DuckTerm than the one it runs.
# Read the version from the source file directly — importing duckterm needs
# an installed venv, which a fresh release worktree doesn't have (that
# dependency once aborted the build MID-BUNDLE, shipping a partial .app).
VERSION="$(sed -n 's/^__version__ = "\(.*\)"/\1/p' ../src/duckterm/__init__.py)"
[ -n "$VERSION" ] || { echo "could not read version" >&2; exit 1; }
cat > "$CONTENTS/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key><string>${APP_NAME}</string>
  <key>CFBundleDisplayName</key><string>${APP_NAME}</string>
  <key>DucktermEdition</key><string>${EDITION}</string>
  <key>CFBundleIdentifier</key><string>${BUNDLE_ID}</string>
  <key>CFBundleVersion</key><string>${VERSION}</string>
  <key>CFBundleShortVersionString</key><string>${VERSION}</string>
  <key>CFBundleExecutable</key><string>DuckTerm</string>
  <key>CFBundleIconFile</key><string>AppIcon</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>LSMinimumSystemVersion</key><string>${MIN_MACOS}</string>
  <key>NSHumanReadableCopyright</key><string>RubberDuckHQ</string>
</dict>
</plist>
PLIST

echo "==> bundling private Python and tmux"
bash bundle-python.sh "$CONTENTS/Resources"
bash bundle-tmux.sh "$CONTENTS/Resources"
bash bundle-jq.sh "$CONTENTS/Resources"

# Copy only the package and fresh web build. Build tools never import Duckterm.
"${PYTHON:-../.venv/bin/python}" - "$CONTENTS" "$VERSION" "$TEST_BUILD" <<'PYBUILD'
import os, plistlib, runpy, shutil, sys
from pathlib import Path
contents = Path(sys.argv[1]).resolve()
root = Path.cwd().parent
package = contents / 'Resources/python/lib/python3.13/site-packages/duckterm'
shutil.copytree(root / 'src/duckterm', package,
                ignore=shutil.ignore_patterns('__pycache__', '*.pyc', 'dashboard', '_build.py'))
shutil.copytree(root / 'web/dist', package / 'dashboard')
shutil.copy2(root / 'LICENSE', contents / 'Resources/LICENSE-duckterm')
collect = runpy.run_path(str(root / 'scripts/stamp_build.py'))['collect']
values = collect(root, sys.argv[2])
(package / '_build.py').write_text(
    '\n'.join(f'{key} = {value!r}' for key, value in values.items()) + '\n')
with (contents / 'Info.plist').open('rb') as f:
    info = plistlib.load(f)
info['DucktermBundledRuntime'] = True
if sys.argv[3] == '1':
    info['DucktermTestBuild'] = True
    if remote_host := os.environ.get('DUCKTERM_TEST_REMOTE_HOST'):
        info['DucktermTestRemoteHost'] = remote_host
    for key in ('DUCKTERM_PORT', 'DUCKTERM_URL', 'DUCKTERM_HOME', 'DUCKTERM_TMUX_SOCKET'):
        os.environ.pop(key, None)
    os.environ['DUCKTERM_INSTANCE'] = 'test'
    instance = runpy.run_path(str(root / 'src/duckterm/helpers/instance.py'))
    info['DucktermTestPort'] = instance['port']()
with (contents / 'Info.plist').open('wb') as f:
    plistlib.dump(info, f)
PYBUILD

# Optional support recipient is supplied at build time, never committed to source.
# Read the environment directly so the address is never logged or parsed as code.
if [[ -n "${DUCKTERM_SUPPORT_EMAIL:-}" ]]; then
  python3 - "$CONTENTS/Info.plist" <<'PYCONFIG'
import os
import plistlib
import sys
from pathlib import Path
path = Path(sys.argv[1])
plist = plistlib.loads(path.read_bytes())
plist["DuckTermSupportEmail"] = os.environ["DUCKTERM_SUPPORT_EMAIL"]
path.write_bytes(plistlib.dumps(plist))
PYCONFIG
fi

built_minos=$(otool -l "$MACOS/DuckTerm" | awk '/LC_BUILD_VERSION/{f=1} f&&/minos/{print $2; exit}')
if [[ "$built_minos" != "$MIN_MACOS" ]]; then
  echo "error: binary minimum macOS is $built_minos, expected $MIN_MACOS" >&2
  exit 1
fi

echo "==> ad-hoc signing (runs locally; not notarized for distribution)"
"${PYTHON:-../.venv/bin/python}" verify-bundle.py "$APP"
bash sign-bundle.sh "$APP" -

echo "==> done: $APP"
if [[ "$RUN_APP" == 1 ]]; then
  open "$APP"
fi
