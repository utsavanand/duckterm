# DuckTerm Lite for Windows — developer preview

This package runs the existing DuckTerm Lite backend in WSL 2 and opens its
dashboard on Windows. The Mac application, shared backend, and dashboard are
unchanged. This is a Windows x64 developer preview, not a signed public release.

## First run

1. Use Windows 11 x64. In an elevated PowerShell window, run
   `wsl --install -d Ubuntu-24.04` if WSL is not already installed. Restart if
   Windows asks, open Ubuntu, and finish creating a regular Linux user. Do not
   run DuckTerm as root. Existing WSL 2 Ubuntu installations can be used.
2. Inside Ubuntu, install the prerequisites:

   ```sh
   sudo apt-get update
   sudo apt-get install -y python3 python3-venv tmux jq curl git lsof
   ```

3. Install and sign in to your coding agent **inside the same WSL distribution**.
   Keep projects in Linux, for example `~/projects`, for best performance.
4. Extract the Windows package into a directory owned by your Windows user.
   In PowerShell in that directory:

   ```powershell
   .\DuckTerm.Windows.Cli.exe list
   .\DuckTerm.Windows.Cli.exe doctor --distro Ubuntu-24.04
   .\DuckTerm.Windows.Cli.exe install --distro Ubuntu-24.04
   .\DuckTerm.Windows.Cli.exe start --distro Ubuntu-24.04
   ```

If exactly one user distribution is installed, `--distro` may be omitted. Docker
Desktop distributions are never selected. Multiple user distributions require an
explicit choice. WSL 1 is rejected. This package does not enable Windows features,
elevate itself, install Linux packages, or modify an existing distribution's user.

The launcher verifies its backend inside WSL and Windows localhost forwarding
before opening the browser. Login credentials are passed to the browser's local
login endpoint and are never printed by the launcher. Do not share browser login
URLs. Dashboard access uses the existing authenticated server; it is not exposed
on the LAN.

## Sessions, updates, and isolation

- `status` reports whether this package's backend is running without credentials.
- Closing the browser leaves the backend and agent sessions running.
- `restart` explicitly restarts this workspace's backend and reattaches its tmux
  sessions. It does not shut down WSL or stop unrelated DuckTerm instances.
- Install a newer package with `install`, then use `restart` to run its backend.
  Older runtime directories are retained because existing sessions can still use
  their hooks and Python helpers. Installation does not restart a running server.
- Windows reboot, WSL termination, and `wsl --shutdown` terminate Linux processes.
  Stored history remains; live process survival across those events is not promised.

The package uses only these dedicated paths inside the selected Linux home:

| Resource | Location |
| --- | --- |
| Private versioned runtime and install receipt | `~/.duckterm-windows-package/` |
| Lite workspace and history | `~/.duckterm-windows-lite/` |
| Backend log | `~/.duckterm-windows-package/server.log` |
| Local endpoint | `http://127.0.0.1:4317` |
| tmux socket | `duckterm-windows-lite` |

An existing unmarked workspace, unsafe symlink, different server identity, or port
collision is refused. The package does not fall back to a Full/Code server. It
never runs `pipx install`, modifies system Python, changes the Mac app, or migrates
an existing workspace. Two WSL distributions cannot concurrently serve this
package through the same Windows port; choose one.

Hooks are an explicit setup step because they update harness settings. After
installing, use the selected runtime's CLI in Ubuntu (replace HASH with the hash
from `~/.duckterm-windows-package/active.json`):

```sh
DUCKTERM_INSTANCE=windows-lite DUCKTERM_HOME="$HOME/.duckterm-windows-lite" \
DUCKTERM_PORT=4317 DUCKTERM_URL=http://127.0.0.1:4317 \
DUCKTERM_TMUX_SOCKET=duckterm-windows-lite DUCKTERM_EDITION=lite \
"$HOME/.duckterm-windows-package/HASH/bin/duckterm" install-hooks --agent claude-code --global
```

Use `--agent codex` for Codex. Follow the harness's hook trust prompts. The
dashboard's Mac-only guided setup is unavailable in this first browser package.
Native PowerShell agents, automatic WSL provisioning, code signing, and automatic
updates are not included yet.

## Build and verify

The Windows package has its own .NET projects, Python adapter, tests, and workflow.
It uses .NET 10; self-contained publication includes the runtime, so end users do
not need the SDK. The Python server remains zero-dependency.

```sh
dotnet run --project packages/windows/Tests/DuckTerm.Windows.Tests.csproj
python3 -m unittest discover -s packages/windows/python_tests -v
```

Build the backend wheel with the existing `scripts/build_package.sh`. Then on a
machine with the .NET 10 SDK:

```sh
dotnet publish packages/windows/Cli/DuckTerm.Windows.Cli.csproj -c Release \
  -r win-x64 --self-contained true -o packages/windows/out
python3 packages/windows/package.py --wheel dist/duckterm-VERSION-py3-none-any.whl \
  --output packages/windows/out
```

The independent `Windows package` GitHub workflow runs contracts on Windows,
builds a self-contained launcher, and uploads a preview zip. On Linux it tests an
actual wheel install, wrong-port refusal, repeated launch, and a real tmux test
session surviving backend restart. Those checks do not substitute for a real WSL 2
clean-machine test. Before public release, verify first-run setup, authentication,
terminal resize/paste, hooks, sleep/resume, localhost forwarding, restart, and
upgrade on Windows 11. Keep the preview label until that evidence exists.
