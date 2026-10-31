# Install DuckTerm

DuckTerm runs locally. The Apple Silicon Mac app includes its own Python,
backend, dashboard, tmux and hook tools. You only install and sign in to the
coding agents you want to use; Git is needed for Git workstreams. The app does
not change system Python or project virtual environments.

Browser-only use on macOS or Linux needs Python 3.11+, pipx and tmux. Node.js
is needed only for source development and tools or connectors that use it.

## Install and launch

Follow the [README quick start](../README.md#install) for the current release
Mac app download. For the app, move **DuckTerm Lite.app**
to Applications and open it; there is no separate backend installation.
For browser-only use, follow the [CLI setup below](#browser-and-cli-installation). The command is `duckterm`;
the product is DuckTerm.

Open **DuckTerm Lite.app** to start the Lite server and dashboard automatically.
The app finds tools installed in the standard Homebrew and `~/.local/bin`
directories and reuses an existing Lite server if one is running. If another
edition is active, the app explains how to switch; it does not stop that server
automatically. No Terminal command is needed for an ordinary app launch.

The commands below use `duckterm`. In an app-only installation, replace it with
`"/Applications/DuckTerm Lite.app/Contents/Resources/bin/duckterm"`. For Code or
Full, use the corresponding app name. This CLI is also on PATH inside newly
launched DuckTerm agent terminals; installing the app does not replace an
existing global CLI.

Opening the app reconnects terminals that are still running. After a Mac
reboot, saved sessions whose terminals ended remain **Interrupted** until you
right-click a session and choose **Resume**. Opening DuckTerm does not restart
those agents automatically.

Install and sign in to your chosen agent separately:

- [Claude Code](https://code.claude.com/docs/en/setup)
- [Codex](https://github.com/openai/codex#quickstart)
- [GitHub Copilot CLI](https://docs.github.com/en/copilot/how-tos/copilot-cli/install-copilot-cli)

### Codex version

DuckTerm is verified with **Codex 0.155.x**. Codex **0.159 and later are not
supported yet**: they run one shared daemon for every Codex session, so DuckTerm
receives each session's events under whichever session launched first. Sessions
show the wrong state and messages, and questions Codex asks you can be lost.

Check your version with `codex --version`. To stop Codex upgrading itself at
startup, add this as a top-level line in `~/.codex/config.toml` (above any
`[section]` header):

```toml
check_for_update_on_startup = false
```

Codex also has a background updater that this setting may not cover, so recheck
`codex --version` after Codex restarts. If Codex has already upgraded, older
versions stay on disk under `~/.codex/packages/standalone/releases/`, and you can
switch back by pointing the `current` link at one of them:

```sh
ln -sfn ~/.codex/packages/standalone/releases/0.155.1-aarch64-apple-darwin \
  ~/.codex/packages/standalone/current
codex --version
```

Run `duckterm doctor` for dependency, server, hook, and trust diagnostics. Install
hooks for your chosen runtime as shown in the README. Hooks edit the agent's
configuration; they are separate from provider sign-in and connector setup.

## Browser and CLI installation

On macOS or Linux, browser-only use needs Python 3.11+, pipx and tmux.

On macOS with [Homebrew](https://brew.sh) already installed:

```sh
brew install pipx tmux
pipx ensurepath
```

Open a new terminal so `pipx` and installed commands are on your PATH. On Linux,
install Python 3.11+, pipx, and tmux using your distribution's package manager.
Then install the published wheel:

```sh
pipx install https://github.com/utsavanand/duckterm/releases/download/v0.4.187/duckterm-0.4.187-py3-none-any.whl
duckterm serve --edition lite
```

The dashboard opens at **http://127.0.0.1:4300**. Leave that terminal running.
DuckTerm is distributed through [GitHub Releases](https://github.com/utsavanand/duckterm/releases),
not currently PyPI; `pipx install duckterm` is not a supported installation path.

The browser path remains supported for Intel Macs and Linux. On Windows, use
[WSL 2 and a Windows browser](WINDOWS.md); Windows support remains untested.
The Mac app does not require this separate CLI installation.

## Upgrade

Back up your data first:

```sh
duckterm backup
```

For the native app, quit it, replace the app in Applications with the new
release, and reopen it. The backend and private runtimes update together.
If you also have a separately started `duckterm serve` terminal, stop that
server with Ctrl-C before reopening the app. Keep the same edition unless
you intend to switch.

For browser-only installations, download the desired `.whl` from
[Releases](https://github.com/utsavanand/duckterm/releases), then update pipx:

```sh
pipx install --force /absolute/path/to/duckterm-VERSION-py3-none-any.whl
```

Stop the foreground server with Ctrl-C, then run `duckterm serve` to retain the
saved edition (`duckterm serve --edition lite` to select Lite). Reload open
browser tabs to load the new dashboard. A separately installed pipx CLI is
updated separately from the Mac app.

Existing tmux agent terminals survive a server restart. See
[backup and restore](backups.md) before attempting a downgrade: an older release
may not understand a newer database schema.

## Troubleshooting

| Symptom | What to check |
|---|---|
| `duckterm: command not found` | For the app, use its CLI path above. For pipx, run `pipx ensurepath` and open a new terminal. |
| No matching distribution for `duckterm` | Install the GitHub release wheel URL, not the bare PyPI package name. |
| tmux is missing | The app includes tmux; replace an incomplete app with a fresh download. Browser-only users install tmux with their package manager. |
| Agent missing or cannot sign in | Run its CLI directly first; finish its own installation and login. |
| Dashboard does not open automatically | Open http://127.0.0.1:4300 while `duckterm serve` is running. |
| Mac app cannot start the server | Check its diagnostics and whether another edition is running. If bundled files are missing, download a fresh copy of the app. |
| macOS blocks the app | Download a fresh copy from the official release. If it is still blocked, report the exact macOS alert and release version. The official v0.4.180 Lite app is signed and notarized. |
| Old dashboard after an upgrade | Restart the server and reopen the native window or reload the browser tab. |

For a bug report, include the release version, OS, installation method, and the
relevant `duckterm doctor` output. Remove private project paths and account details.

## Uninstall

```sh
pipx uninstall duckterm
```

The pipx command applies only if you installed the browser CLI. Remove the
DuckTerm app from Applications to uninstall the native app. Data under
`~/.duckterm` is retained. To remove hooks, run
`duckterm uninstall-hooks --agent AGENT --global` for each configured agent
**before** removing the app or CLI, using the app's CLI path if applicable.
