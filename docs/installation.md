# Install DuckTerm

DuckTerm runs locally on macOS or Linux. The dashboard needs Python 3.11+,
tmux, and an installed, signed-in coding agent. Node.js is only needed for
source development and connectors that use it, not the packaged dashboard.

## Install and launch

Follow the [README quick start](../README.md#install) for the current release
wheel and Mac app download. Install with pipx to keep DuckTerm isolated from
other Python packages. The command is `duckterm`; the product is DuckTerm.

`duckterm serve` runs in the foreground and opens http://127.0.0.1:4300.
Keep its terminal open while using the browser. The optional Mac app can start
the server for you and reuses an existing server if one is running.

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

## Upgrade

Back up your data first:

```sh
duckterm backup
```

Download the desired `.whl` from [Releases](https://github.com/utsavanand/duckterm/releases),
then replace the installed package using its local path:

```sh
pipx install --force /absolute/path/to/duckterm-VERSION-py3-none-any.whl
```

Quit the Mac app or stop the foreground server with Ctrl-C, then reopen the app
or run `duckterm serve`. Reload open browser tabs to load the new dashboard.
When upgrading the native app, also replace the old app in Applications with
the app from the matching release. The CLI and native shell are separate downloads.

Existing tmux agent terminals survive a server restart. See
[backup and restore](backups.md) before attempting a downgrade: an older release
may not understand a newer database schema.

## Troubleshooting

| Symptom | What to check |
|---|---|
| `duckterm: command not found` | Run `pipx ensurepath`, open a new terminal, then run `pipx list`. |
| No matching distribution for `duckterm` | Install the GitHub release wheel URL, not the bare PyPI package name. |
| tmux is missing | Install tmux with your package manager and rerun `duckterm doctor`. |
| Agent missing or cannot sign in | Run its CLI directly first; finish its own installation and login. |
| Dashboard does not open automatically | Open http://127.0.0.1:4300 while `duckterm serve` is running. |
| Mac app cannot start the server | Verify `duckterm serve` works in a terminal and that the CLI is installed with pipx. |
| macOS blocks the app | The build is not notarized. After attempting to open it, review Privacy & Security in System Settings, or use the browser. |
| Old dashboard after an upgrade | Restart the server and reopen the native window or reload the browser tab. |

For a bug report, include the release version, OS, installation method, and the
relevant `duckterm doctor` output. Remove private project paths and account details.

## Uninstall

```sh
pipx uninstall duckterm
```

Remove DuckTerm.app from Applications if installed. Data under `~/.duckterm`
is retained. To remove hooks, run `duckterm uninstall-hooks --agent AGENT --global`
for each configured agent **before** uninstalling the CLI.
