# DuckTerm.app for macOS

A native desktop window around the local dashboard, using WKWebView. It appears
in the Dock and supports standard macOS clipboard shortcuts and notifications.
Closing the last window quits the app. A server started by the app stops when
the app quits; a server started separately is left running.

## Install

Use the [README Mac app instructions](../README.md#mac-app). The published
Apple Silicon archive requires macOS 13+ and the separately installed `duckterm`
CLI, tmux, and your agent CLIs. It is ad-hoc signed, not notarized. The app and
CLI should come from the same release.

## Build from source

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
