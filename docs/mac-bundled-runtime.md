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

Agent CLIs, Git, and tmux are still external tools. Install tmux with
`brew install tmux`. ServerProcess exposes `missingTmux` for native first-launch
messaging; that UI is a separate UI-dev handoff and must be integrated before
claiming the complete first-launch guidance is shipped. The dashboard itself
can start without tmux. Bundling tmux is deferred.

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
