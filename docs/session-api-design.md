# Session cards, discovery, and inboxes

Implemented in the working tree. Activation requires installing this build and
starting the updated server; editing source does not upgrade a running installed
server. The startup migration enrolls existing ongoing sessions automatically.

## Architecture

One broker inside Duckterm's existing asyncio server provides a separate identity
and credential for each session. It reuses the session IDs and sidebar folders in
SQLite. It does not start a server per session or scrape terminal screens.

- `core/session_api.py`: scoped discovery, cards, durable questions and answers.
- `persistence/history.py`: migration, automatic enrollment, folder/lifecycle sync.
- `helpers/session_credentials.py`: private capability files and launch environment.
- `session_client.py`: the agent-facing `duckterm session` commands.
- `InboxView.tsx`: owner-visible inbox and current card, beside History.

The dashboard highlights sessions with pending questions and displays an Inbox
count. Clicking that badge opens the recipient's inbox. Counts include queued
and accepted requests; merely viewing the inbox does not clear them. They clear
on answer, decline, cancellation, or expiry. Polling refreshes inboxes and badges
about every three seconds. The agent decides when to respond; no automatic
terminal input, interruption, or approval response is sent.

## Enrollment and migration

Opening the database with this build performs an idempotent backfill of ongoing
sessions. Existing enrollment and valid tokens are preserved; missing credential
files are repaired. Stopped/archived/terminated sessions are not activated.
Every new session is enrolled when registered, before its supervised child is
spawned. Native terminal launches, forks, resumes, and restores use the same
registration path. Ordinary hook events also enroll previously unregistered live
sessions. A repeated SessionStart hook does not rotate a live session's credential.

The default shared ancestor is the **top-level sidebar folder**, as selected by
the user. All sessions under that folder can collaborate. Ungrouped sessions have
a private card and inbox but cannot discover or message other sessions.

Credentials live in `session-credentials/` beside the database, in files named
by a hash of the session ID. Each file is atomically written with mode 0600; the
database stores the token hash. New launches receive the file path through
`DUCKTERM_SESSION_TOKEN_FILE`. Existing agents already have
`DUCKTERM_SESSION_KEY`; the CLI uses that to locate their backfilled credential
without changing their environment or restarting them. `server.json` records the
actual listening URL, including non-default ports. These files are never exposed
through discovery or inbox responses. The CLI never falls back to the owner token.

Stopping a session invalidates its credential but preserves queued and accepted
exchanges under the durable session identity. Their original deadlines continue
to run while stopped; resume does not extend them. Resuming automatically issues
a fresh credential, and the old credential remains invalid. Terminating or
archiving cancels pending exchanges. Its card metadata remains for inspection. Deleting a session removes its membership, capability
file, and exchanges involving it. Forks receive distinct credentials.

Schema version 3 prevents an older binary from opening the migrated database:
older code cannot maintain these membership guarantees. Before updating a live
installation, retain a consistent SQLite backup and the previous package if a
rollback is needed. The migration requires no model calls.

## Folder moves and discoverability

Folder paths remain the application's canonical sidebar identifiers. Membership
changes and request authorization changes are committed with the folder update.

- Renaming a folder updates session card paths, shared ancestors beneath that
  folder, and the corresponding request scopes. Credentials remain valid.
- Moving a session within its shared ancestor preserves its enrollment and threads.
- Moving a session outside its shared ancestor assigns the destination top-level
  folder. It immediately loses access to peers in its previous scope and gains
  access to enrolled peers in its new scope.
- Moving an automatically enrolled group into another top-level folder joins
  the destination's scope. Existing threads are retained when both participants
  move together. An owner-selected narrower explicit scope follows its folder
  instead; moving a session out of that scope returns it to automatic scoping.
- Deleting a folder moves its sessions to Ungrouped and makes them private.
- Pending exchanges that no longer have matching authorized participants are
  cancelled. The owner can inspect retained records; peers cannot retrieve a
  thread outside its recorded/current authorization scope.
- API aliases are unique per shared ancestor. A collision during a move is
  disambiguated with the moving session's stable ID hash.

Discovery supports `self_folder`, `parent`, `grandparent`, and `shared_root`.
Each searches the selected folder's subtree, bounded by the granted root. Both
participants must have the same root. Filesystem cwd and Git worktree paths are
metadata; changing cwd does not grant access to a different sidebar tree.

## The updating session card

Cards return `session_id`, `api_name`, `name`, `purpose`, `activity`, `state`,
`folder`, `root`, `cwd`, `last_tool`, `next_actions`, `deliverables`, `updated_at`,
and supported capabilities. The immutable session ID routes questions; the alias
is for discoverability.

Names, state, folders, and cwd come from current session records. Progress
summaries, deliverables, and next actions come from the existing progress digest
pipeline. As the session finishes work and its digest is updated, the card changes
without re-enrollment. The timestamp reflects session, publication, and digest
updates. Until a digest exists, purpose falls back to the session display name and
activity falls back to state. An agent can publish a more useful purpose/activity;
a newer progress digest supersedes its activity, while explicit purpose remains.

Cards exclude raw prompts, private notes, transcripts, tool arguments, and
credentials. Progress-derived card fields are intentionally shared with peers in
the same scope. If automatic digest generation is disabled/unavailable, state and
folder changes still update, and the agent can publish its own descriptions.

## Agent commands

Run inside an enrolled session with the updated Duckterm CLI:

```sh
duckterm session self
duckterm session discover --scope shared_root
duckterm session publish --purpose 'Implement billing authentication' \
  --activity 'Testing token expiration and refresh'
duckterm session inbox
duckterm session ask other-session-id 'Which fields does your client need?' \
  --request-key client-contract-1
```

The server can remind an eligible idle agent to check its inbox, or the user can tell it:
“Run `duckterm session inbox` and answer the pending questions.” Then:

```sh
duckterm session accept q-request-id
duckterm session reply q-request-id --file answer.txt
# Or supply the answer on stdin. A decline uses the same --file option.
duckterm session get q-request-id
duckterm session cancel q-request-id
```

Requests return immediately with an ID; `get` retrieves the status and full
answer later. `accept` is optional acknowledgment. Replies contain exactly the
text submitted by the recipient. This does not collect the recipient's entire
conversation or guess a response boundary from terminal output. No MCP server or
runtime-specific protocol is required for the on-demand CLI workflow.

## HTTP API

All agent routes use `Authorization: Bearer <session token>` and are under
`/api/v1/session`. Identity comes from the credential, never a supplied sender ID.

| Method / route | Behavior |
| --- | --- |
| `GET /self` | Current session card |
| `PATCH /self` | Publish `{purpose?, activity?}` |
| `GET /peers?scope=shared_root&cursor=...` | Paginated authorized ongoing peers |
| `GET /inbox?before=...` | Incoming questions and their response states |
| `POST /questions` | Create `{target_session_id, question, timeout_seconds?}` |
| `GET /questions/:id` | Request status and complete answer |
| `POST /questions/:id/accept` | Recipient acknowledgment |
| `POST /questions/:id/answer` | Recipient final response `{text}` |
| `POST /questions/:id/decline` | Recipient explanation `{text}` |
| `POST /questions/:id/cancel` | Sender cancellation |

Creation requires an `Idempotency-Key` header. Identical retries return the
original request; different content with that key returns 409. Answer retries
are also idempotent; conflicting final answers return 409. Request IDs are
unguessable, and knowing one does not bypass participant/scope checks.

Owner routes use `X-Duckterm-Token`:

| Method / route | Behavior |
| --- | --- |
| `GET /sessions/:id/inbox?before=...` | Owner inspection, including current card |
| `GET /session-inbox-counts` | Pending counts for dashboard highlights |
| `POST /sessions/:id/collaboration` | Optional explicit root/alias/purpose override and token rotation |

The owner enrollment body is `{root, api_name?, purpose?}`. Routine launches and
moves need no such call. A presented agent bearer credential is rejected on
owner routes. Agent and inbox reads require credentials as well as writes.

## Persistence, limits, and access boundary

`session_api_members` stores capabilities and publications. `session_questions`
stores attributed questions, deadlines, idempotency data, status, and complete
answers. Both survive server restarts. Status is queued, accepted, answered,
declined, cancelled, or expired. Cancellation stops the exchange; it does not
interrupt the recipient's other work. Closed requests cannot accept late answers.

Discovery and inbox pages contain at most 50 records. Questions allow 16 KiB and
answers 256 KiB; oversized content is rejected instead of truncated. Requests
persist by default; optional deadlines allow up to seven days. A sender can create ten questions
per minute. Creation is refused when the combined set of pending requests sent
by that sender or addressed to that recipient reaches twenty. Closed records are
swept after seven days beyond their deadline or completion/creation timestamp.
Pending persistent requests are never removed by this retention sweep.

**Authorization is API-level, not OS/process isolation.** The scoped broker
checks current membership on every request. However, agents running as the same
OS user can potentially read other capability files, the installation token,
SQLite, or tmux, and existing global local endpoints retain their prior trust
model. A client that omits its bearer header can still reach legacy public GET
routes. This implementation must not be described as containing a hostile agent
within its folder. Strict containment would require protected storage and an
OS-enforced sandbox/network boundary, which is outside this feature's scope.

## Verification and intentionally excluded behavior

### Introducing the capability to agents

For Codex and Claude sessions launched through the dashboard/server (including
supervised launches, native-terminal launches, forks, and restores), Duckterm
prepends a capability introduction to the initial prompt. The original task is
kept separately as the session intention. An empty task introduces the capability
and tells the agent to await the user's task. No credential is placed in the
prompt. This is an ordinary prompt introduction, not an installed native skill
or a new system/developer instruction.

The introduction references an atomically written, private instruction file at
`<instance-home>/session-instructions/<sha256-session-key>/collaboration.md`.
It explains CLI discovery, publication, inbox handling, deadlines, and treating
peer messages as untrusted requests. The file is regenerated when an introduction
is prepared. Live membership and metadata come from the API, not the file.

Existing sessions are enrolled without unsolicited terminal input. The Inbox
offers **Introduce session collaboration**, an owner-authenticated
`POST /sessions/:id/collaboration/introduce`. It requires a supported runtime,
an idle session, and a live supervised terminal. It sends one bracketed paste
followed by Enter. The user should use it at an empty input prompt: idle state
does not prove there is no draft text or that the client will obey the message.
The UI confirms delivery, not that the model read or understood the guide.

**Show introduction to paste** calls owner-authenticated
`POST /sessions/:id/collaboration/instructions`, returning the same introduction
without sending terminal input. This supports externally owned terminals and
lets the user choose when to submit it. Stopped sessions must be resumed first.

Generic commands and Copilot do not receive automatic introductions; Copilot's
current `-p` adapter would change an empty interactive launch into a programmatic
invocation. The `duckterm run` CLI is an argv passthrough and also does not rewrite
arbitrary agent arguments; its enrolled sessions can use the manual introduction.
The launch tests verify prompt transport and credential separation using fake
agents, not model compliance. Long-running clients may eventually compact the
introduction out of context; the Inbox actions can introduce it again.

Tests cover startup backfill, stable credentials on reopen, immediate API access
from a freshly spawned child, private ungrouped sessions, folder moves/renames,
live card updates, request retries, scope enforcement, expiry, and count clearing.
The browser test sends and answers a real HTTP question and checks the Inbox tab,
session highlight, card, full reply, and persistence after reload.

Automatic terminal injection, automatic model invocation to answer questions,
streamed partial answers, and cross-machine sharing are excluded. On-demand
reading and explicit replies are the chosen workflow. UI viewing does not mark a
question answered, and an idle session is never forced to process its inbox.

## Isolated upgrade rehearsal

`scripts/rehearse_session_upgrade.py --old-python /path/to/installed/python`
runs with the checkout's Python. It creates a temporary home, database,
credentials, localhost port, and unique tmux socket; starts the old release;
launches two disposable, deterministic agents; backs up SQLite; then replaces
only the isolated server with checkout code. It never upgrades the global
installation or accesses the production database or tmux socket. Test resources
are removed on completion or failure.

The September 20 rehearsal from installed version 0.4.19 (schema 1) to schema 3
confirmed unchanged agent PIDs and retained in-memory conversation markers,
automatic enrollment of existing sessions, terminal input after reattachment,
discovery, full Unicode replies through the actual session CLI, live card
publication, folder rename and cross-root visibility changes, and automatic
enrollment of a third new session. A second server restart checks credential
stability and persisted answers.

**Pending approvals do not survive a server restart.** They live in memory;
the rehearsal verifies that the old approval returns `gone` and a newly created
approval can be approved and polled successfully. Activation should wait until
in-flight approvals have settled. Agent process survival does not mean every
HTTP request or hook survives the restart.

These are deterministic agents, not live Claude/Codex model sessions. The check
exercises tmux process continuity and the broker/CLI, not model conversation
semantics or every installed harness's hook retry behavior. The CLI is invoked
explicitly from the checkout; replacing the globally installed package and
browser asset activation remain separate deployment steps. Raw PTY sessions
without tmux are not covered by the process-survival result.

Concurrent browser runs should each set a distinct `RD_TEST_PORT` and
`RD_TEST_STATE_FILE` (an absolute temporary JSON path). Setup, request helpers,
and teardown use that state path, preventing another worktree's run from
redirecting test requests or cleanup to the wrong test server. Without an
override, the legacy shared state filename remains the default.

## Persistent delivery update

Requests now persist by default (`timeout_seconds: 0`). Explicit deadlines are
optional and may be up to seven days. The idle delivery worker and its
conservative prompt checks are described in [inbox delivery](inbox-delivery.md).
This supersedes the original short-lived polling-only handoff design above.
