# Remote workspace operations

The native app's **Computer → Connect to computer…** menu switches between this
Mac and saved SSH hosts. Add the `duckterm-dev` alias configured in `~/.ssh/config`.
Verify new hosts in Terminal first; the app never accepts unknown or changed
host keys. SSH credentials remain in your SSH configuration/agent. The selected
computer is remembered; notifications follow that computer.

Closing the app disconnects SSH. It does not stop the remote service or agents.
The title reports connection failure separately from agent state. Files and
terminals belong to the selected computer; local working directories and
connectors are not copied to it.

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
Then, through administrator SSH on the connector VM:

```sh
sudo /opt/duckterm/venv/bin/duckterm connector-admin github --version=1
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
