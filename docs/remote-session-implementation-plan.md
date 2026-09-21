# Remote sessions and connector security

Status: approved by the user; implementation and QA in progress in `remote-session`.

Infrastructure update: the user subsequently authorized GCP project creation
and initial provisioning. The live environment and completed infrastructure
checks are recorded in [gcp-development-environment.md](gcp-development-environment.md).
Application implementation and its QA remain separate from this completed setup.

## Outcome and scope

Duckterm connects to a persistent Linux computer belonging to one user. The
user starts multiple agents there, closes their laptop, and later reconnects
to the same terminals. Each agent has its own conversation and optionally its
own git worktree. All agents on that computer share the enabled integrations.

This version includes remote connection management and the agreed connector
security fixes. It does not include per-agent permissions, automatic migration
of a running local session, team sharing, billing, a public relay, or a
one-click cloud provisioning product. A reproducible Linux installation and
service setup is included. Linux compute stays running while agents work;
automatic suspension is deferred until its interaction with background jobs
and agent state has been tested.

## Worktree and approval

- Implement in `/Users/utsava/.duckterm/worktrees/duckterm/remote-session`, branch
  `remote-session`. This is already separate from the main checkout.
- After approval, fast-forwarded `remote-session` to committed main `db59b04`.
  Recheck both checkouts before any Git mutation.
- The main checkout currently has uncommitted session-API and inbox work,
  including overlapping server, orchestrator, and frontend files. Leave that
  work untouched. Integrate its committed form when available; do not merge
  into the dirty checkout or automatically stash/commit someone else's changes.
- Implementation and the initial GCP deployment are authorized.
- After implementation and QA, update against latest main, resolve conflicts,
  rerun affected checks, and merge into main as the user requested. Preserve
  unrelated work; never reset a dirty checkout. Remote push/release is separate
  from the requested local merge.

## Proposed architecture

Mac app/browser -> managed SSH tunnel -> remote Duckterm server -> tmux agents.
Agent MCP shims -> connector service -> external services.
Connector service -> managed secret store.

Use the existing tmux supervisor, SQLite history, hooks, approvals, and terminal
WebSocket transport. Keep the remote dashboard bound to loopback. SSH host keys
must be verified; never silently accept a changed key. Let users use existing
SSH configuration and an SSH agent rather than saving private keys in SQLite.

The connector service runs under a separate unprivileged OS identity, owns its
MCP child processes, and is the only component allowed to retrieve hosted
connector secrets. Agent users have no sudo, Docker socket, broker files, or
cloud credential/metadata access. Shared integration access does not grant
secret-store administration. Broker administration and tool execution must be
different authenticated interfaces; agent-facing shims expose no secret-read,
enable, or policy-write methods.

Proposed first hosted secret backend: Google Cloud Secret Manager, behind a small
SecretStore interface. Deployment must supply a narrowly scoped service
identity, without putting reusable cloud access keys in agent homes or
environments. Validate that agents cannot retrieve the service identity through
instance metadata. If the selected deployment cannot provide this boundary,
use a separately hosted broker rather than weakening it. The compute provider
and test account/region will be recorded before live provisioning; no claim of
real cloud QA can be made without an actual test environment.

Initial compute recommendation: an on-demand GCP e2-medium in us-central1,
4 GiB RAM, with a persistent Linux disk. Start with lightweight concurrent agents
and resize if QA demonstrates memory/CPU pressure. The free e2-micro is useful
for smoke tests, not the multi-agent acceptance target. Confirm the complete
compute, disk, networking, and broker estimate before provisioning; trial credit
eligibility is account-specific. No AWS account is required.

Agent-provider authentication is separate from external connector credentials.
Use the provider CLI's official remote authorization flow once per provider on
the persistent host: Codex device-code login and Claude Code browser-link login.
The user completes authorization in their local browser. Reuse the host's login
for subsequent agents and reconnects; detect expiry and request renewal. Do not
silently switch subscription users to separately billed API access or clone
local credential caches by default. Codex documents cache transfer as a fallback;
Claude setup-token is an alternative with different capabilities and a long-lived
credential, not a zero-consent login shortcut.

## Implementation sequence

### 1. Establish the baseline and remote session path

- Bring this branch up to date; install development dependencies and run the
  existing baseline checks. Record pre-existing failures separately.
- Add host connection records containing name, SSH target, local forwarding
  port, and status, without secrets. Add Local/Remote selection to the Mac app
  and route its dashboard and notification polling to the selected host.
- Manage SSH forwarding lifecycle, errors, reconnect/backoff, and port conflicts.
  App exit closes its tunnel, never the remote server or agents.
- Provide Linux bootstrap/service configuration for Duckterm, tmux, and supported
  agent CLIs. Keep agent processes alive across Duckterm service restarts;
  explicitly verify service-manager cgroup/termination behavior.
- Preserve terminal replay and session state after reconnect. Bound terminal
  log growth. Distinguish disconnected, waiting, stopped, and terminated.
- Document host reboot as process loss. Resume only where the harness supports
  it; otherwise expose a clear stopped/relaunch state. Never silently launch a
  second copy of an already-running agent.

### 2. Make connector identity and credential storage explicit

- SQLite stores connector ID, selected credential source, account identity,
  enabled state, write mode, secret reference, and validation status. It stores
  no plaintext token, OAuth refresh token, or private key.
- Replace implicit GitHub CLI precedence with an explicit source choice. Resolve
  and display the selected account; show unverified/offline rather than claiming
  successful validation when the provider cannot be reached.
- Keep local CLI-login reuse available when explicitly selected. Hosted broker
  connectors use service-owned credentials, not an agent user's ambient login.
- Implement the hosted secret backend and broker-controlled stdio MCP bridges
  for the existing GitHub, Railway, and Porkbun connectors. Verify Railway's
  supported noninteractive authentication in a service-owned home before
  declaring support; never copy the user's entire CLI home as a workaround.
- Use restricted credentials supported by each provider. GitHub App installation
  tokens are the preferred hosted strategy; a selected, scoped token can serve
  the initial test deployment. Do not label a provider as supporting automatic
  rotation or revocation until its actual API flow is verified.
- Replace macOS command-line secret arguments with native Keychain API calls
  through a maintained binding/helper compatible with the package and Mac app.
- For explicitly enabled local/test file storage, create files as 0600 from the
  first write, directories as 0700, refuse symlinks, and use atomic replacement.
  Never silently fall back to this backend for hosted secrets.
- Migrate existing settings conservatively. Preserve explicitly enabled access,
  identify ambiguous GitHub credential choices for user review, and verify
  migrated secrets before removing old entries. Do not copy secrets into backups
  or committed migration fixtures.

### 3. Implement connector lifecycle and explicit write access

- Enable/disable is enforced by the service, not merely an MCP config edit.
- Disable first rejects new calls, then closes active connections and terminates
  the connector's service-owned child processes. Stale shims cannot reconnect
  until it is enabled again. Already completed external effects cannot be undone.
- Keep authorization after disable for convenient re-enable. A separate Forget
  action removes Duckterm's stored credential; it is not provider revocation.
- Revoke authorization calls a supported provider endpoint where available;
  otherwise provide the exact provider-side revocation steps and report pending
  status honestly. Never claim an existing gh/Railway login was revoked merely
  because Duckterm removed an MCP registration.
- New Porkbun connections default to read-only. Make write access an explicit
  setting. Inspect the server's actual read-only behavior and enforce permitted
  tools in the bridge if needed. Existing write-enabled installations retain
  their state with a visible label. Pin the tested MCP dependency versions.
- Log identity, lifecycle changes, and call outcomes without secret values or
  unrestricted tool arguments/results. Prevent secrets entering argv, dashboard
  responses, ordinary logs, or build artifacts. Credentials needed by an MCP
  child may exist in its restricted process environment; do not describe them
  as inaccessible to that process or to the machine administrator.

### 4. Product integration

- Show remote host identity and connection state prominently.
- Connector UI shows account, credential source, validation state, enabled
  status, and write mode for the selected host.
- Explain that these integrations are shared by agents on that host. Do not
  introduce per-session connector profiles or a misleading permission picker.
- Keep local workflows functional; existing sessions may need an explicit MCP
  reconnect/restart to adopt migrated registration paths.

## QA and acceptance matrix

| Layer | Required evidence |
|---|---|
| Unit | Explicit credential selection, no ambient fallback, validation failure states, metadata-only persistence, migration/rollback, secret file permissions and symlink rejection, write-mode defaults. |
| Connector integration | Real bridge with fake upstream MCP: initialize/list/call, concurrent sessions, disable during a call, child termination, stale reconnect denial, re-enable, rotation, broker restart, and secret-store outage. |
| Access boundary | Agent OS user cannot read broker files, environments, secret store, cloud metadata credentials, or administrative API. Another host/user cannot use its connector identity. |
| Secret leakage | Synthetic canary secret absent from argv, SQLite, generated configs, API responses, logs, snapshots, and artifacts. Do not use real secrets in automated fixtures. |
| UI | Local/remote switching, correct remote connector identity, failed login/tunnel, write toggle, disable versus forget/revoke, disconnected versus stopped states. |
| Real Linux lifecycle | Run at least two agents concurrently in distinct worktrees; disconnect SSH and close the app; confirm progress remotely; reconnect and verify same process IDs and terminal content. |
| Restart/recovery | Restart Duckterm and its service without killing agents; restart broker and verify recovery; reboot the host and verify honest stopped/resume behavior and no duplicate agents. |
| Live providers | Read-only calls to dedicated GitHub/Railway/Porkbun test resources, expired credential, disconnect, and supported revocation. Test writes only against designated disposable resources with explicit authorization. |
| Soak | Overnight remote run with reconnect cycles; record duration, memory/log growth, process continuity, and failures. A shorter run must be reported as incomplete soak coverage. |
| Regression | Full scripts/check.sh: Python lint/format/types/tests, frontend lint/types/unit tests, built-dashboard Playwright. Also build and manually exercise the native Mac app, including dialogs and notifications. |

Broker access tests are required even though we are not separating one user's
agents from each other. SQLite configuration is not authoritative permission
state if an agent can write it; protected broker state and its administration
boundary enforce connector access.

## Completion and merge

Deliver a working local-to-remote flow, reproducible deployment instructions,
migration/rollback notes, and a QA record listing commands, environments, versions,
observed results, and untested cases. Do not equate mocks with live-provider QA.
Treat unavailable required credentials or infrastructure as an explicit remaining
acceptance item, not a passed check.

After required checks pass, review the diff for secret exposure and unrelated
changes, incorporate latest main, rerun affected validation, and merge into main.
Report the merge commit and any operational follow-up. Do not deploy/release
automatically as part of the merge.

## References

- Factory persistent machines: https://docs.factory.ai/droid-computers/overview
- Superset host/relay model: https://docs.superset.sh/remote-access
- Cursor HTTP MCP credential separation: https://cursor.com/docs/cloud-agent/capabilities
- OWASP secret lifecycle: https://cheatsheetseries.owasp.org/cheatsheets/Secrets_Management_Cheat_Sheet.html
- GCP secret-store practices: https://docs.cloud.google.com/secret-manager/docs/best-practices
- GCP compute pricing: https://cloud.google.com/products/compute/pricing/general-purpose
- Codex remote authentication: https://learn.chatgpt.com/docs/auth
- Claude Code authentication: https://code.claude.com/docs/en/authentication
- Apple Keychain APIs: https://developer.apple.com/documentation/security/using-the-keychain-to-manage-user-secrets
