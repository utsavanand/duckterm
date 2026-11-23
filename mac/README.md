# DuckTerm.app for macOS

A native desktop window embeds the Duckterm dashboard. It owns a local backend
or connects to a remote workspace through a managed SSH tunnel.

## Test changes before promotion

Build and open the test application from the feature worktree:

```sh
mac/build.sh --test --run
```

The result is `mac/build/DuckTerm Test.app`, with a purple duck and **TEST**
badge. Its Dock name, app menu, and window titles identify it as DuckTerm Test.
Add `--edition=lite` or `--edition=code` for DuckTerm Lite Test or Code Test.
Every edition and its Test build has its own bundle ID, home, tmux socket and
port, resolved by `src/duckterm/helpers/instance.py --desktop-profile` and written
to Info.plist, so all six can run side by side with separate saved host
preferences, notifications, and WebKit data.

Both production and Test builds package the private Python runtime, this
worktree's backend, a fresh dashboard, tmux and hook tools. The local Test
Full backend uses the `test` profile, with
`~/.duckterm-test`, its own derived port, and the `duckterm-test` tmux socket.
It never falls back to the installed production CLI. Test bundles are for local
QA, not redistribution; their backend runs with the same private interpreter
and dependency layout as production.

Model logins and provider CLI configuration remain those of the current Mac
user. Connecting to a remote host shows that host's actual sessions and
integrations; the Test badge does not create a separate remote environment.
Use the development alias `duckterm-dev` for remote QA.

1. Build DuckTerm Test from the feature worktree and run automated checks.
2. Exercise the change in DuckTerm Test, including switching computers and
   closing/reopening the app. Record failures and fix them in the worktree.
3. After user acceptance and required QA pass, reconcile and merge into main.
4. Build the normal app from validated main with `mac/build.sh`. Use the existing
   release process to install/distribute it. Do not rename the test bundle into
   production or replace the installed app during QA.

Build outputs are separate: building Test preserves `DuckTerm.app`, and
building production preserves `DuckTerm Test.app`.

## Start a remote session

Click **New session**, then choose the destination under **Run on**. Pick
**This Mac** or a saved **Remote** computer. For a new remote Git checkout, follow
**Repository → Agent & task → Review**, then **Launch session**. DuckTerm creates
a separate workspace automatically; no folder path is required. Other project
options retain copying a local project or choosing an existing folder.
The remote session opens alongside local sessions in the same dashboard.

For the configured development deployment, build with:

```sh
DUCKTERM_TEST_REMOTE_HOST=duckterm-dev mac/build.sh --test --run
```

This seeds the development host when the Test app has no saved connections.
Add and manage other hosts under **Settings → Remote computers**. Verify a new host's SSH key
and authentication first. There is no Command-Shift-K shortcut; another app
may register that key combination globally.

Closing the app disconnects its tunnel, not remote agents. Reopening remembers
the last computer. **This Mac** in the Test build shows local test sessions.
The candidate supports reviewed project copy/clone and Move to remote for stopped
sessions with supported exact conversation IDs. Candidate live VM validation
and user acceptance remain required before promotion.

See [remote workspace operations](../docs/remote-workspace-operations.md).

## Build requirements and checks

Build on Apple Silicon with a Swift toolchain and macOS SDK, preferably full
Xcode, plus the repository's `.venv` and `web/node_modules`. The first build
downloads pinned, checksum-verified CPython and native dependency archives.
It compiles tmux and jq for macOS 13 with static third-party dependencies and
retains their licenses. Later builds reuse verified source archives.

The build checks every native component's architecture, minimum macOS version
and library references, then ad-hoc signs nested code and the app. Developer ID
signing uses `mac/sign-bundle.sh APP IDENTITY`; notarization remains a release step.

```sh
swift test --package-path mac
mac/build.sh --test
mac/build.sh
```

Native builds copy the checked-in icon for the selected edition and Test flag.
Lite uses the duckling, Code uses the laptop duck, and Full keeps the original.
Test icons retain the purple duck and **TEST** badge. A missing selected icon
stops the build; another edition's icon is never substituted. Builds do not
regenerate or modify tracked icon resources.

When changing an approved SVG, regenerate its production and Test resources
with Node and `web/node_modules/playwright` (including its Chromium browser):

```sh
node mac/make-icon.mjs --edition=lite
node mac/make-icon.mjs --edition=lite --test
```

Use `--edition=code` or `--edition=full` for the others. The edition argument is
required. Add `--output=/absolute/path/review.icns` to render a review copy
without replacing the checked-in resource.

## Install

Use the [README Mac app instructions](../README.md#mac-app). The published
Apple Silicon archive requires macOS 13+ and your separately installed coding
agents. Python, tmux, hook tools and the backend are included. It is signed with
a Developer ID and notarized by Apple. The app leaves project Python environments
and a separately installed global DuckTerm CLI unchanged.

## Production build from source

Use a working Swift toolchain and macOS SDK (Xcode or compatible Command Line
Tools). From the repository root:

```sh
./mac/build.sh
open mac/build/DuckTerm.app
```

The build targets arm64 macOS 13 and reads its version from
`src/duckterm/__init__.py`. It produces an ad-hoc-signed app at
`mac/build/DuckTerm.app`. Use `--edition=lite` or `--edition=code` for the other
named bundles. Intel users can use the browser-only Python installation.

If the SDK reports duplicate `SwiftBridging` modules, select a compatible Xcode
toolchain. No separately installed Python CLI is used by the built app.

## Verify

1. Launch the app; verify its Dock icon and dashboard window appear.
2. Open a session and verify terminal input, copy, and paste.
3. Verify the file editor and native prompt dialogs work.
4. Allow notifications and verify a supported agent's waiting state triggers one.
5. Quit and relaunch; verify the dashboard reconnects to existing agent terminals.

A broadly distributed installer should use Developer ID signing and notarization;
these are not supplied by the current local build script.

## Complete app updater

Native builds resolve pinned Sparkle through Swift Package Manager and embed
its framework/helpers. Settings updates the whole app bundle. See
[Mac update architecture and release procedure](../docs/development/mac-updates.md)
for public-key configuration, signed feeds, and isolated Test validation.
Development builds without an update public key run with updates unavailable.

## Distribution installer

Website and emailed installation links use an edition-specific DMG. Open the
image, drag the app to Applications, then open the copied app from Applications.
Finder handles any existing-app replacement confirmation. The installer does
not move files automatically or change DuckTerm session data.

Keep signed ZIPs and their appcasts for in-app Sparkle updates. After signing,
notarizing, and stapling each app, build both formats from that same bundle:

```sh
# Use the matching edition name and release version for each of Lite, Code, Full.
mac/archive-bundle.sh "$APP" "$RELEASE_DIR/DuckTerm-Full-$VERSION-macos-arm64.zip"
python3 mac/build-installer.py "$APP" "$RELEASE_DIR/DuckTerm-Full-$VERSION-macos-arm64.dmg"
```

The DMG builder requires a logged-in Finder session and may request permission
to configure Finder. It changes only the temporary image window. It verifies
bundle signatures and checks every packaged file against the input. It never
launches the app or overwrites an existing output.

Use the existing release identity and notarization profile to sign the final
DMG, submit it to Apple's notarization service, and staple its ticket. Credentials
remain in the release environment; the builder never creates or reads them.
`python3 scripts/verify_mac_installers.py --archives "$RELEASE_DIR" --version "$VERSION"`
checks all three final DMGs, their tickets and Gatekeeper approval, their
Applications shortcuts and window layouts, and equality with the ZIP contents.
`scripts/release.sh prod` requires this verification and publishes versioned
DMGs plus stable DMG and ZIP aliases. Publish all assets before switching website
and email links to the DMG aliases. Older ZIP links stay available.

Run `python3 mac/Tests/installer-bundle.py` on a Mac with Finder to exercise the
real disk-image pipeline using an ad-hoc-signed disposable Test app. It never
installs or launches that app. CI's headless unit tests cover rejection and
mount cleanup; the release Mac must run the real Finder test as well.
