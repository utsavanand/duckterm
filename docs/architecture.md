# RubberTerm — Architecture

Status: current as of v0.4.27 (2026-09-21). What runs today, not a proposal.
Origin decisions: [terminal-forward-design.md](terminal-forward-design.md).

## Shape

One Python asyncio process (no web framework), SQLite for persistence, tmux
for session survival, a React/TypeScript dashboard, and an optional Swift
WKWebView Mac shell. Agents are the user's own CLIs (`claude`, `codex`, …)
spawned into PTYs the server owns; they report structured events through
their own hook systems.

```
┌────────────────────────── browser / Mac WKWebView ─────────────────────────┐
│  React dashboard: GridView · Terminal (xterm.js) · Messages · AgentTree    │
│  Approvals · FleetChat · InboxView · Connectors · Harnesses               │
└───────▲───────────────────────▲───────────────────────────▲───────────────┘
        │ binary WS (PTY bytes, │ HTTP JSON (loopback-gated  │ SSE events
        │ keystrokes, resize)   │ GET, token-gated POST)     │
┌───────┴───────────────────────┴───────────────────────────┴───────────────┐
│ server.py — asyncio HTTP/WS server (transport/httpio.py, websocket.py)    │
│                                                                            │
│ core/orchestrator.py  SessionSupervisor: spawn agent in PTY or tmux pane,  │
│                       pump raw bytes, write_input, detect_state            │
│ core/eventbus.py      fan-out of events to SSE subscribers                 │
│ core/approvals.py     in-memory approval registry (decide endpoints,       │
│                       zombie expiry) — does NOT survive restart            │
│ core/progress.py      per-session progress digest pipeline                 │
│ core/session_api.py   session-collaboration broker (cards/discover/inbox)  │
│                                                                            │
│ runtimes/  claude_code · codex · copilot · generic  (Harness adapters)     │
│ persistence/ history.py (SQLite, schema 3) · checkpoints · digests ·       │
│              snapshots                                                     │
│ llm/       fleet-chat insights · AGENTS.md suggest · summarizer            │
│ git/       worktrees · forks · repo detection                              │
│ agents/    tmux + terminal control, hooks_install, mcp_install             │
│ connectors.py  GitHub/Railway/Porkbun MCP wiring + secret store            │
│ helpers/   security · session_credentials · session_instructions · paths   │
└───────▲────────────────────────────────────────────────────────────────────┘
        │ POST /events (hook HTTP calls)          ▲ `duckterm session` CLI
┌───────┴────────────────┐                ┌───────┴────────────────┐
│ agent CLI in tmux PTY  │  fires hooks   │ session_client.py      │
│ (user's subscription)  │ ──────────────▶│ (agent-facing broker   │
└────────────────────────┘                │  client, bearer token) │
                                          └────────────────────────┘
```

## Components

**Transport (`transport/`)** — hand-rolled HTTP (`httpio.py`) and WebSocket
(`websocket.py`). The WS is binary, bidirectional, with ping/pong. Limits
(from the 2026-09-20 security review): 32 KiB headers, 8 MiB general bodies,
2 MiB session-API bodies, 1 MiB inbound WS frames, 10 s request-read
deadline; duplicate headers, invalid lengths, and unsupported transfer
encodings rejected.

**Orchestrator (`core/orchestrator.py`)** — `SessionSupervisor` spawns the
agent into a PTY or tmux pane, pumps raw byte chunks to the terminal WS, and
keeps a decoded rolling buffer solely for `detect_state`/tool detection. The
two consumers are separate: raw passthrough to xterm.js, text buffer for
state. Input goes back via PTY write / tmux `send-keys`; resize via
`TIOCSWINSZ`.

**Harness adapters (`runtimes/`, `harnesses.py`)** — one class per agent CLI
owning both drive (`launch_command`, `detect_state`, `locate_transcript`,
`read_transcript`) and observe (`hook_spec`). The `REGISTRY` in
`harnesses.py` is the single source of truth for the agent picker,
`install-hooks`, runtime construction, and checkpoint transcript reads.
Shipped: `claude-code`, `codex`, `copilot`, `generic`. Per-harness
capabilities live on the contract, not behind `isinstance` checks in
endpoints (RETRO: the empty Messages tab bug). "Harness" also names
installable suites (`duckterm-harness.json`) — see
[harnesses.md](harnesses.md).

**Events and state** — hooks installed by `duckterm install-hooks`
(`hooks/duckterm-hook.sh`) POST to `/events`: session start, tool use,
sub-agent start/stop, permission requests. The server derives state badges,
resolves approvals, and builds the sub-agent tree from them. The dashboard
subscribes over SSE; context-pressure numbers are read from the agent's
transcript file on disk, not from terminal scraping.

**Persistence (`persistence/`)** — SQLite (schema 3): sessions, folders,
history, digests, checkpoints, session-API membership
(`session_api_members`) and questions (`session_questions`). Schema
versioning blocks older binaries from opening a migrated database.
`snapshots.py` bundles recently-active sessions to disk. Progress digests
(`core/progress.py` + `persistence/digests.py`) feed both fleet chat and
session cards.

**Session collaboration (`core/session_api.py`)** — a broker inside the same
process, one bearer credential per session (0600 files under
`session-credentials/`, DB stores the token hash; path handed to launches
via `DUCKTERM_SESSION_TOKEN_FILE`). Scope = the session's top-level sidebar
folder; ungrouped sessions are private. Durable questions/answers with
idempotency keys, 16 KiB / 256 KiB size caps, 10 questions/min per sender,
20 pending cap, 5–15 min deadlines, 7-day sweep. Agent routes under
`/api/v1/session` (bearer); owner routes use `X-Duckterm-Token`. Full
behavior, folder-move semantics, and the upgrade rehearsal:
[session-api-design.md](session-api-design.md).

**LLM layer (`llm/`)** — the only place RubberTerm itself calls a model:
fleet-chat answers, AGENTS.md rule suggestions, digest summarization.
Prompts making claims about a person carry an explicit evidence bar
(RETRO: the digest-personality incident).

**Connectors (`connectors.py`)** — one switch per service (GitHub, Railway,
Porkbun) that registers the service's MCP server into claude-code/codex user
configs. Tokens never land in plaintext harness configs: GitHub's entry runs
through `duckterm connector-run github`, which resolves the token at launch
(gh CLI first, then macOS Keychain / 0600 file store) and passes it via the
environment.

**Frontend (`web/`)** — React + xterm.js (+ fit addon). `Terminal.tsx` owns
the WS; `gridLayout.ts` implements folder grids/splits; `useEventStream.ts`
consumes SSE; `useInboxCounts.ts` polls inbox badges (~3 s). Dev servers and
e2e always serve `web/dist` over the packaged copy (RETRO: stale-bundle
afternoon), and the e2e suite builds what it tests.

**Mac shell (`mac/`)** — WKWebView around the same URL with the full trio
wired: main menu, clipboard bridge (`__rtCopy`/`__rtPaste` — xterm renders
selection on canvas, so responder-chain copy is inert), WKUIDelegate
dialogs. Retries the URL until its self-started server answers.

## Data locality and backup

There is no Postgres and no distributed element. All state is one machine:

- `~/.duckterm/db.sqlite` — sessions (ongoing and archived), folders,
  events/history, checkpoints metadata, collaboration memberships and
  question threads. Single writer, WAL mode (`-wal`/`-shm` files live
  beside it — copying `db.sqlite` alone while the server runs is not a
  valid backup; use SQLite's `.backup`).
- `~/.duckterm/` also holds session credentials, checkpoints, snapshots,
  worktrees, logs. `~/.duckterm/backups/` contains only ad-hoc
  pre-migration DB copies; `duckterm snapshot` bundles the last hour's
  active sessions. Both live on the same disk as the original.
- Conversation transcripts are NOT in duckterm — they live in the agents'
  own directories (`~/.claude/projects/…`, `~/.codex/…`) and are what
  forks and context-pressure reads depend on.

Consequence, stated plainly: if the machine is lost, every session record,
history row, inbox thread, checkpoint, and transcript is lost with it,
except code pushed to git remotes and whatever machine-level backup
(Time Machine) covers. Live sessions' processes can never survive box loss;
their durable remainder is exactly DB rows + transcripts + git state. The
GCP remote workspace is a second independent instance (its own
`~/.duckterm`, its own DB), not replication. Untracked working-tree files
are equally exposed on a smaller scale: a `git clean` from any session
deletes them (this happened to this doc set on 2026-09-21; recovered from
the authoring session's context).

Backup approach (decided 2026-09-21, minimal — see roadmap):

- **Local box**: a `duckterm backup` command producing one consistent
  archive (SQLite `.backup` + checkpoints + the agent transcript dirs),
  written to a user-chosen path and optionally uploaded to a GCS bucket in
  the existing GCP project using the user's own `gcloud` auth. Session
  credential files are excluded — they are secrets and re-issuable on
  resume. Scheduling is the OS's job (launchd/cron), with Time Machine as
  the baseline.
- **Remote workspace VM**: GCP scheduled persistent-disk snapshots — a
  GCP-side resource policy, zero code, and no cloud credentials on the VM
  (which by design has none).
- Not building: Postgres, replication, an in-app backup scheduler/UI, or a
  restore wizard — restore is documented as "put the archive back, start
  the server" until real use demands more.

## Security model

- Bind loopback only; require a loopback Host and same-origin (including
  port) for state-changing routes and WebSockets. GETs are loopback-gated;
  POSTs owner-token-gated; session-API routes bearer-gated with identity
  from the credential, never a supplied sender ID.
- Terminal command construction uses `shlex.quote`; dashboard and AGENTS.md
  file access check resolved-path ancestry (`Path.is_relative_to`) and
  symlink targets.
- Secrets are written atomically with mode 0600; empty tokens are
  regenerated and never compare equal.
- Boundary honestly stated: authorization is API-level. Same-OS-user agents
  can read other capability files, SQLite, or tmux. Containing a hostile
  agent requires an OS-enforced sandbox, which is out of scope (candidate
  direction on the roadmap).

## Remote workspace (in progress — branch `remote-session`, not merged)

Runs the same server on a persistent per-user GCP **workspace VM** (multiple
agents/worktrees, shared enabled integrations, no per-agent isolation),
attached to from the Mac over SSH. Constraints decided and verified so far:

- The workspace VM has **no cloud service account**. A separate **connector
  VM** is the only thing that reads Secret Manager — exactly its three
  entries, via attached identity, no downloaded key.
- Connector access is fixed MCP bridges over private **mutual TLS**; the
  client cert grants execution only, not secret or admin access. Broker
  policy is root-owned; admin is SSH/IAP only; no public dashboard or
  broker ingress. Model auth stays on the workspace VM.
- Disabling a connector closes active streams and blocks reconnect;
  forgetting stored credentials and provider-side revocation are distinct
  actions. Porkbun writes are opt-in.
- tmux persistence holds across SSH disconnects and service restarts
  (verified, unchanged PIDs) — but a VM **reboot terminates processes**;
  continuous execution across reboot is not claimed.
- Known operational gaps: manual TLS renewal, SQLite retention planning.

## Stack decisions and their triggers

- **Python/asyncio backend, kept.** The job is spawn-PTY, shuttle bytes, fan
  out events, talk to SQLite/git for a handful of local agents. Trigger to
  revisit: profiling shows the byte-pump is the bottleneck at ≈20+ busy
  terminals — then a small PTY-relay sidecar, not a rewrite.
- **No web framework, hand-rolled transport.** One local single-user server;
  the security review hardened the hand-roll with explicit limits.
- **tmux for persistence.** Sessions survive server restarts with unchanged
  PIDs (verified by the upgrade rehearsal). Raw PTY sessions without tmux do
  not get this guarantee.
- **stream-json rendering: not used.** Hooks + transcript files already give
  the structured data; revisit only for a second, review-panel surface.
- **No duckterm scheduler for PM routines.** The planned PM/backlog loop
  ([pm-routines-design.md](pm-routines-design.md)) composes existing
  mechanisms: Claude Code's `/loop` in a tmux-persistent session for
  scheduling, `BACKLOG.md` for proposal state, waiting-state notifications
  for owner approval, `duckterm session ask` for dispatch. Trigger to build
  server-side pieces: terminal approval proves annoying in practice (then a
  Backlog tab + one `proposals` table), or routines are needed for a
  runtime with no self-scheduling and cron is unmanageable (then a
  scheduler).
