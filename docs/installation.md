# Install RubberTerm

RubberTerm runs locally on macOS or Linux. The dashboard needs Python 3.11+,
tmux, and an installed, signed-in coding agent. Node.js is only needed for
source development and connectors that use it, not the packaged dashboard.

## Install and launch

Follow the [README quick start](../README.md#install) for the current release
wheel and Mac app download. Install with pipx to keep RubberTerm isolated from
other Python packages. The command is `duckterm`; the product is RubberTerm.

`duckterm serve` runs in the foreground and opens http://127.0.0.1:4300.
Keep its terminal open while using the browser. The optional Mac app can start
the server for you and reuses an existing server if one is running.

Install and sign in to your chosen agent separately:

- [Claude Code](https://code.claude.com/docs/en/setup)
- [Codex](https://github.com/openai/codex#quickstart)
- [GitHub Copilot CLI](https://docs.github.com/en/copilot/how-tos/copilot-cli/install-copilot-cli)

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

Remove RubberTerm.app from Applications if installed. Data under `~/.duckterm`
is retained. To remove hooks, run `duckterm uninstall-hooks --agent AGENT --global`
for each configured agent **before** uninstalling the CLI.
