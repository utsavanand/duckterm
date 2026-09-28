# Self-contained Mac backend

Apple Silicon builds now include CPython 3.13.15, the DuckTerm backend and the
built dashboard. Users do not need Python or pipx to open the Mac app.
The runtime is the pinned 20260924 python-build-standalone install-only stripped
archive; `mac/bundle-python.sh` verifies its fixed SHA-256 before extracting it.
Source: https://github.com/astral-sh/python-build-standalone/releases/tag/20260924
Archive format: https://gregoryszorc.com/docs/python-build-standalone/main/distributions.html

Startup order:

1. Reuse an existing DuckTerm server answering on the instance port (4300 for
   production). Its version can differ until that server is restarted.
2. Otherwise launch `Contents/Resources/python/bin/python3.13` with the bundled
   backend. No developer checkout path is searched.
3. Only if there is no bundled interpreter, allow a developer build to find an
   installed CLI in standard locations or the login shell. Test builds never
   fall back to a production CLI.

Both production and test builds snapshot this checkout's dashboard and Python
source. Runtime writes go to the instance data directory; bytecode writes and
user-site imports are disabled, and inherited PYTHONHOME/PYTHONPATH cannot
redirect the bundled interpreter. Embedded Mach-O files are ad-hoc signed before
the app is signed. Developer ID signing and notarization remain release work.
Intel/universal bundles are not supplied by this build.

## Remaining external tools

Agent CLIs and Git remain external tools. Apple Silicon apps include tmux 3.7c
with statically linked libevent 2.1.13 and utf8proc 2.12.0. The only dynamic
libraries are macOS system libraries; Homebrew is not required. Licenses ship
in `Contents/Resources/tmux/licenses`.

The backend selects the bundled client first, then a working system client if
the bundle cannot execute or its protocol cannot talk to the existing server.
Both use the same instance socket; changing clients never changes session
identity. Unknown socket errors fail rather than treating live panes as gone.
SessionStart records the selected binary and source. CLI-only installations
continue to use system tmux. The native missing-tmux screen remains UI-dev's
fallback when neither client works; that visual handoff is separate.

Bundling makes DuckTerm responsible for tmux and embedded-library security
updates. At each release, review the pinned versions and upstream advisories,
update hashes deliberately, rebuild, and rerun the compatibility test. Official
sources and SHA-256 pins are in `mac/bundle-tmux.sh`; every cache hit is verified.
The build uses the macOS system allocator (`--disable-jemalloc`), system ncurses,
and a macOS 13 deployment target. It never copies Homebrew build artifacts.

## CLI and hooks

The bundled `Contents/Resources/bin/duckterm` wrapper is first on the server's
PATH, so agents launched by this app can run DuckTerm hooks and session commands
without pipx. It resolves paths relative to the app and supports paths with spaces.

For agents launched independently in a terminal, either keep the existing pipx
installation or explicitly create a symlink after moving the app to Applications:

```sh
mkdir -p ~/.local/bin
ln -s /Applications/DuckTerm.app/Contents/Resources/bin/duckterm ~/.local/bin/duckterm
```

This intentionally refuses to overwrite an existing executable. Put
`~/.local/bin` on your shell PATH. Moving/deleting the app later breaks this
optional symlink; repoint it to the new location. The app does not silently
install or replace a global CLI, and no new install-shim UI is introduced.

## Build and verification

Build requirements remain Swift/Xcode, Node dependencies, and a build-time
Python environment: `mac/build.sh` builds the dashboard and production app;
`mac/build.sh --test` builds an isolated test app using the same bundled runtime.
`DUCKTERM_TEST_INSTANCE` can choose a unique test data/port/tmux namespace.
A cached archive may be supplied through `DUCKTERM_PYTHON_ARCHIVE`; its checksum
is still verified, including on cache hits.

Run `python3 mac/Tests/bundled-runtime.py mac/build/DuckTerm.app` on a Mac with
GUI access. It relocates a copy, selects a private test instance, runs the real
native app with an empty home and stripped PATH, verifies its bundled Python
child and dashboard HTTP 200, tests the optional CLI symlink, and verifies the
signature stayed intact. The temporary app/home/database are removed. This is
an isolated local test, not a notarized-download/Gatekeeper acceptance test.

The native smoke test also disables host tool lookup in its copied test bundle,
launches a `test:true` terminal, and confirms the launch event names bundled
tmux. `PYTHONPATH=src .venv/bin/python mac/Tests/bundled-tmux.py
/path/to/bundled/tmux /path/to/system/tmux` verifies an existing private server
and pane keep the same PIDs. All fixture processes and data are removed.
