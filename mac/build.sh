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
# Names, bundle IDs, homes, sockets and ports come from the backend's
# stdlib-only resolver, run as a file, so the app and CLI can never disagree.
PROFILE_ARGS=(--desktop-profile "$EDITION")
ICON_COMMAND="node make-icon.mjs --edition=$EDITION"
case "$EDITION" in
  full) ICON_NAME="AppIcon" ;;
  lite) ICON_NAME="AppIconLite" ;;
  code) ICON_NAME="AppIconCode" ;;
esac
if [[ "$TEST_BUILD" == 1 ]]; then
  PROFILE_ARGS+=(--test)
  ICON_NAME="${ICON_NAME}Test"
  ICON_COMMAND+=" --test"
fi
[[ -s "Resources/$ICON_NAME.icns" ]] || {
  echo "error: missing $EDITION icon Resources/$ICON_NAME.icns; regenerate it with $ICON_COMMAND" >&2
  exit 1
}
PROFILE_JSON="$("${PYTHON:-../.venv/bin/python}" ../src/duckterm/helpers/instance.py "${PROFILE_ARGS[@]}")"
PROFILE_SHELL="$("${PYTHON:-../.venv/bin/python}" - "$PROFILE_JSON" "$EDITION" <<'PYPROFILE'
import json, shlex, sys
try:
    profile = json.loads(sys.argv[1])
except ValueError:
    sys.exit("helpers/instance.py --desktop-profile returned no profile")
fields = {"profile_id": str, "edition": str, "home_name": str, "tmux_socket": str,
          "port": int, "bundle_id": str, "app_name": str}
for key, kind in fields.items():
    if not isinstance(profile.get(key), kind) or profile[key] in ("", 0):
        sys.exit(f"desktop profile field {key} is missing or invalid: {profile.get(key)!r}")
if profile["edition"] != sys.argv[2]:
    sys.exit(f"desktop profile edition {profile['edition']} does not match {sys.argv[2]}")
if profile.get("instance_id") is not None and not isinstance(profile["instance_id"], str):
    sys.exit("desktop profile instance_id must be a string or null")
print(f"PROFILE_ID={shlex.quote(profile['profile_id'])}")
print(f"APP_NAME={shlex.quote(profile['app_name'])}")
print(f"BUNDLE_ID={shlex.quote(profile['bundle_id'])}")
PYPROFILE
)"
eval "$PROFILE_SHELL"
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
# Edition icons are generated and reviewed together with their SVG sources.
# Normal builds copy the exact checked-in resource, never rewrite source assets
# or fall back to another edition when an icon is missing.
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
"${PYTHON:-../.venv/bin/python}" - "$CONTENTS" "$VERSION" "$TEST_BUILD" "$PROFILE_JSON" <<'PYBUILD'
import json, os, plistlib, runpy, shutil, sys
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
profile = json.loads(sys.argv[4])
info['DucktermProfile'] = profile['profile_id']
info['DucktermHome'] = profile['home_name']
info['DucktermTmuxSocket'] = profile['tmux_socket']
info['DucktermPort'] = profile['port']
if profile['instance_id']:
    info['DucktermInstance'] = profile['instance_id']
if sys.argv[3] == '1':
    info['DucktermTestBuild'] = True
    if remote_host := os.environ.get('DUCKTERM_TEST_REMOTE_HOST'):
        info['DucktermTestRemoteHost'] = remote_host
# The app CLI defaults to this app's profile; see Resources/duckterm-cli.
cli = contents / 'Resources/bin/duckterm'
stamped = cli.read_text().replace('@DESKTOP_PROFILE@', profile['profile_id'])
if '@DESKTOP_PROFILE@' in stamped or profile['profile_id'] not in stamped:
    sys.exit('could not stamp the app CLI profile')
cli.write_text(stamped)
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
