# Remote workspace operations

Use **RubberTerm Test** (`mac/build.sh --test --run`) for feature QA. Its purple
duck and TEST badge distinguish it from the production app. Follow the
[test-to-production workflow](../mac/README.md) before promotion.

Choose **New session → Run on → This Mac / Remote** to start an agent on the
selected computer. The form stays mounted and preserves its agent/name/prompt;
the folder picker always browses the destination. The dashboard changes only
after successful launch. Cancel leaves it unchanged. Manage saved connections
under **Settings → Remote computers**. Verify new hosts in Terminal first;
the app never accepts unknown or changed host keys. SSH credentials remain in
SSH configuration/agent. The selected computer is remembered; notifications
follow that computer.

Closing the app disconnects SSH. It does not stop the remote service or agents.
The title reports connection failure separately from agent state. Files and
terminals belong to the selected computer; local working directories and
connectors are not copied to it.

## Model login prerequisites

Before starting Codex device login, enable **Device code authorization for
Codex** in ChatGPT → Settings → Security. If that setting is disabled, OpenAI's
consent page disables Continue and never reaches code entry. Repeatedly issuing
new codes does not fix that. Enable the setting first, then start one fresh
`codex login --device-auth` on the remote account and use that attempt's code.
The CLI must report success; browser account sign-in alone is not confirmation.
Verify afterward with `codex login status`.

Claude uses `claude auth login --claudeai`; paste its browser-returned code
directly into the remote login terminal. Verify with `claude auth status`.
Neither flow requires copying the laptop's credential cache. Both authenticate
the persistent `duckterm` OS user, not the administrative SSH user.

Install Codex's matching `codex-code-mode-host` with `infra/linux/install-tools.py`.
Ubuntu 24.04 also needs the distribution `bubblewrap` package and its AppArmor
profile, configured by `infra/gcp/bootstrap.sh`. Keep the host-wide unprivileged
namespace restriction enabled. Verify a real sandboxed terminal command as well
as login; a text-only model request does not exercise these prerequisites.

## Service lifecycle

`infra/linux/install-workspace.sh` installs a built source distribution and the
systemd service. Agent processes run as `duckterm`, never the administrative
OS Login user. `KillMode=process` deliberately preserves the tmux server across
service stop/restart. Stop agents through Duckterm before maintenance that must
end their work. A VM reboot terminates running processes; it preserves disk
history and model login files. Do not describe reboot recovery as continued
execution of the same process.

Pane output spools retain at most two 8 MiB generations per session. Older raw
output is discarded. tmux's screen/scrollback supplies terminal reconnects.
Conversation and event history remain in SQLite and need separate retention or
backup planning for sustained production use.

## Connector boundary

The connector VM authenticates the workspace using a TLS client certificate.
That certificate grants shared connector execution, not Secret Manager access
or administrative actions. The broker's attached service account can read only
the three explicitly granted Secret Manager entries. The agent VM has no
attached service account. The broker has no public connector ingress.

Policy is a root-owned JSON file readable by the broker, not writable by it.
Only an administrator SSH session can change credentials, enable, or disable.
The agent-facing dashboard displays status but cannot administer this policy.
This first development interface deliberately uses the separate administration
CLI; a trusted desktop administration panel is still needed for product polish.

Add a secret version through GCP Console or a secure local prompt that sends
JSON through stdin to `gcloud secrets versions add --data-file=-`. Do not paste
secrets into chat, shell arguments, source files, or the agent terminal.
GitHub/Railway payloads are `{"token":"…"}`; Porkbun also needs `"secret":"…"`.
The account or workspace Railway API token is required, not a project token.
`infra/gcp/connect-github.py` offers explicit reuse of the Mac's GitHub CLI
authorization or a separate token, displays the verified identity, then uploads
directly to Secret Manager. Reusing authorization shares permissions and provider
revocation with the Mac. This development project's active GitHub version is 2;
version 1 was a synthetic QA credential and has been destroyed.
Then, through administrator SSH on the connector VM:

```sh
sudo /opt/duckterm/venv/bin/duckterm connector-admin github --version=2
sudo /opt/duckterm/venv/bin/duckterm connector-admin porkbun --version=1
# Enable writes only when explicitly intended:
sudo /opt/duckterm/venv/bin/duckterm connector-admin porkbun --version=1 --write-access
sudo /opt/duckterm/venv/bin/duckterm connector-admin github --disable
```

Version references are numeric. To rotate, add a new version and select it with
`connector-admin`; the policy change closes old streams. Disable prevents new
connections and closes broker-owned processes; it cannot undo a provider action
already accepted. Revoke the credential separately at its provider, and disable
or destroy obsolete Secret Manager versions after validating the replacement.

The development TLS certificates expire after one year. Rotate the certificates
and CA deliberately before expiration; there is no automatic certificate
renewal in this development setup. Keep the signing key off both VMs. Revoking
workspace access requires removing its entry from the broker policy, which also
closes its active connections.

All agents on a workspace share access. Local connector preferences do not
isolate processes belonging to the same OS user. Provider model logins remain
on the workspace and are separate from hosted connector credentials.

## Validation

Run `scripts/check.sh`, `swift test --package-path mac`, and `mac/build.sh`.
Use `infra/linux/verify-workspace.py start` as `duckterm`, disconnect SSH, then
run `verify` from a new SSH connection. Restart the systemd workspace service
and verify the same process IDs and active session rows. Complete real-provider
login/tool tests and the overnight run before merging this feature into main.
