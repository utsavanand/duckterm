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
Its bundle ID is `com.rubberduckhq.rubberterm.test`, so it can run alongside
DuckTerm with separate saved host preferences, notifications, and WebKit data.

The test build snapshots this worktree's Python backend and built dashboard into
the bundle. Build the dashboard first when changing frontend code (`scripts/check.sh`
builds and verifies it). The local backend uses `DUCKTERM_INSTANCE=test`, with
`~/.duckterm-test`, its own derived port, and the `duckterm-test` tmux socket.
It never falls back to the installed production CLI. The build uses this Mac's
Python interpreter and is for local QA, not redistribution.

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
**This Mac** or a saved **Remote** computer. The agent, task name, and prompt
stay in the same form; choose a folder on the destination computer before launching.
The underlying dashboard changes only after the new session starts successfully.
Folders and sidebar groups from one computer are not silently reused on another.

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

Use a Swift toolchain with the macOS SDK, preferably full Xcode. The build
compiles with `swiftc` and ad-hoc signs the app. Distribution signing and
notarization belong to the release process.

```sh
swift test --package-path mac
mac/build.sh --test
mac/build.sh
```

The icon generator requires Node and `web/node_modules/playwright`. Test icon
rendering must succeed or have a previously generated `AppIconTest.icns`; the
build must not silently use the production icon.

## Install

Use the [README Mac app instructions](../README.md#mac-app). The published
Apple Silicon archive requires macOS 13+ and the separately installed `duckterm`
CLI, tmux, and your agent CLIs. It is ad-hoc signed, not notarized. The app and
CLI should come from the same release.

## Production build from source

Use a working Swift toolchain and macOS SDK (Xcode or compatible Command Line
Tools). From the repository root:

```sh
./mac/build.sh
open mac/build/DuckTerm.app
```

The build uses `swiftc` for the current machine's architecture and reads its
version from `src/duckterm/__init__.py`. It produces an ad-hoc-signed app at
`mac/build/DuckTerm.app`. Building on Intel produces an Intel app; the
published arm64 archive is for Apple Silicon only.

If the SDK reports duplicate `SwiftBridging` modules, select a compatible Xcode
toolchain. The Python CLI must be installed before the app can start its server.

## Verify

1. Launch the app; verify its Dock icon and dashboard window appear.
2. Open a session and verify terminal input, copy, and paste.
3. Verify the file editor and native prompt dialogs work.
4. Allow notifications and verify a supported agent's waiting state triggers one.
5. Quit and relaunch; verify the dashboard reconnects to existing agent terminals.

A broadly distributed installer should use Developer ID signing and notarization;
these are not supplied by the current local build script.
