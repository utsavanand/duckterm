# Session discovery and questions

Status: proposed contract, September 20, 2026. This document describes new
behavior; the session API is not implemented yet.

## Objective

A running session can discover other ongoing sessions in its permitted folder
tree, learn their published purpose and current activity, and ask one a question.
The recipient answers in its existing conversation, and the complete answer to
that question returns to the requester with an explicit completion signal.

Use one broker in the existing Duckterm server. Each session has its own API
identity and permissions, rather than its own listening process or port. The
broker owns discovery, authorization, delivery, and request history. Runtime
adapters own how a particular agent receives a question and submits its answer.

This is local session collaboration, separate from the remote human sharing
proposal in [session-sharing-design.md](session-sharing-design.md).

## Repository findings that constrain the implementation

- `HistoryStore` already stores session keys, names, intentions, working
  directories, runtime, activity state, and folder membership (`grp`). Reuse
  these identities instead of creating a second session registry.
- Sidebar folders currently use mutable slash-separated names. `move_folder`
  renames both a folder and its descendants. Authorization needs stable folder
  IDs and parent relationships so rename and move have distinct meanings.
- `SessionSupervisor.write_input` and `write_bytes` can write to a supervised
  terminal. They provide neither a prompt transaction nor a correlated reply.
  Native terminal launches can also have a tracked row without a supervisor
  capable of accepting that input.
- Runtime state detection and `Stop` events are useful activity signals. They
  do not prove that a particular question was received or answered. Transcript
  readers return conversations, but do not expose a uniform request/turn mapping.
- The existing server allows local GETs without authentication. Hooks read the
  installation-wide token and submit events with a caller-supplied session key.
  A new scoped endpoint alone cannot establish isolation from those paths.
- There is no common runtime contract for delivering a message safely while a
  user types, an agent runs a tool, or an approval prompt is open.

## Session identity and published metadata

Example response from the proposed discovery API:

```json
{
  "session_id": "new-42f37b809ae6d152",
  "api_name": "billing-auth",
  "name": "Fix billing authentication",
  "purpose": "Implement and verify service-to-service authentication for billing.",
  "folder": {
    "id": "fld_billing",
    "path": "payments/backend/billing"
  },
  "workspace": {
    "cwd": "/work/payments-billing",
    "repository": "/work/payments"
  },
  "runtime": "claude-code",
  "activity": {
    "state": "busy",
    "summary": "Checking token refresh behavior in integration tests.",
    "updated_at": "2026-09-20T20:00:00Z"
  },
  "capabilities": ["questions.receive", "answers.explicit"],
  "availability": "online"
}
```

`session_id` is the existing immutable session key and the routing identity.
`api_name` is a readable alias, unique within the collaboration root; renaming
it cannot redirect an existing request. `name` is the existing display name.
`purpose` is a short, explicitly published description; do not automatically
publish the initial prompt, which can contain private instructions or secrets.
Likewise, activity summaries are published by the session rather than copied
from terminal output. Show their timestamp so old descriptions remain visibly
old. Unpublished fields are null, not inferred from private transcripts.

Folder membership, workspace paths, runtime, liveness, and capabilities come
from the broker. Sessions may update only their own alias, purpose, and activity
summary. Display names remain owner-managed initially. Discovery excludes
transcripts, notes, tool arguments, approvals, credentials, and raw output.

## Folder scope

Working assumption: “folder” means the Duckterm sidebar hierarchy. Filesystem
directories remain descriptive metadata. This keeps worktrees in the same
logical project even when their disk paths are siblings in a different place.

At launch, the owner chooses a **collaboration root**: a folder that contains
the session. The default is the session's own folder. An ungrouped session has
only self access until the owner assigns a root. A session cannot enlarge its
root, move itself, or grant another session access.

For a session in `payments/backend/billing` with root `payments`:

| Requested scope | Selected folder | Visible candidates |
| --- | --- | --- |
| `self_folder` | `payments/backend/billing` | Ongoing members of its subtree |
| `parent` | `payments/backend` | Ongoing members of its subtree |
| `grandparent` | `payments` | Ongoing members of its subtree |
| Another ancestor above the root | None | Denied |
| An unrelated folder | None | Denied |

Scope is computed from server-side membership, never a client-provided cwd or
path prefix. Discovery also requires the target to participate in the same
collaboration root. This prevents a broad-root session from inspecting a
session that opted into a narrower, private collaboration group. Both parties
must opt into the same root to discover and communicate with each other.

The session can select an ancestor to expand its search, but cannot exceed the
owner's grant. No implicit “two levels up from cwd”: that can turn a repository
boundary into an entire workspace or home directory.

Use stable folder IDs and validate parent links without cycles. Renames
preserve identity. Moving a session or folder across its authorized root
suspends affected memberships and pending deliveries in the same transaction.
Rejoining requires an owner grant. Check current membership on discovery,
enqueue, delivery, event replay, and reply retrieval. Knowing a session or
request ID never bypasses those checks. Unauthorized target lookups return the
same 404 as unknown targets.

If filesystem scope is preferred instead, keep it a separate policy mode:
resolve symlinks and `..`, use path-component ancestry, and grant an explicit
canonical root. Worktree membership must be owner-assigned; a shared Git remote
or repository name is not an authorization proof.

## Proposed API, version 1

Agent endpoints require a session credential on every request, including GETs.
The broker derives the caller identity from that credential. Request bodies
cannot supply or override the sender identity.

| Method and route | Meaning |
| --- | --- |
| `GET /api/v1/session/self` | Identity, published metadata, root, capabilities |
| `PATCH /api/v1/session/self` | Publish own alias, purpose, activity summary |
| `GET /api/v1/session/peers?scope=parent&cursor=...` | Paginated permitted ongoing peers |
| `GET /api/v1/session/peers/:id` | Published metadata for an authorized peer |
| `POST /api/v1/session/questions` | Enqueue a question addressed to a peer |
| `GET /api/v1/session/questions/:id` | Request status and complete answer, if available |
| `POST /api/v1/session/questions/:id/cancel` | Requester cancels further delivery/waiting |
| `GET /api/v1/session/inbox` | Recipient's pending questions |
| `POST /api/v1/session/questions/:id/accept` | Recipient acknowledges a delivered question |
| `POST /api/v1/session/questions/:id/answer` | Recipient submits the explicit final answer |
| `POST /api/v1/session/questions/:id/decline` | Recipient declines with a reason |
| `GET /api/v1/session/events` | Authorized events with durable replay cursor |

Creation request:

```json
{
  "target_session_id": "new-42f37b809ae6d152",
  "question": "What token refresh contract should my client implement?",
  "timeout_seconds": 300
}
```

Require `Idempotency-Key` on creation; scope it to the authenticated sender.
Reusing the key with identical content returns the original request. Reusing
it with different content returns 409. Return 202 with an immutable request ID,
`queued` status, deadline, and status URL after committing the request.

The request ID accompanies delivery, acknowledgment, response chunks, and the
final answer. The answer endpoint accepts `{text, delivery_id}`; only the
authenticated recipient holding the current delivery may answer. Identical
retries return the stored answer; conflicting final answers return 409.
Final answers are immutable. Follow-up questions create new request IDs.

Use JSON errors `{error: {code, message}}`. Distinguish malformed input (400),
bad credentials (401), unknown/inaccessible resources (404), state conflicts
(409), oversized content (413), rate limits (429), and unsupported delivery
(422). Deadlines are request states, not ambiguous HTTP connection failures.

## Delivery and complete answers

The broker commits a question before attempting delivery. One question at a
time may occupy a recipient's conversation; further questions remain queued.
User work and approvals take precedence. A busy session need not be interrupted.

The runtime contract must expose delivery capabilities explicitly:

1. A structured message transport, where available, submits the question and
   returns a native turn ID. Its adapter maps that turn's completion and answer
   to the broker request. Verify this for each runtime before advertising it.
2. A cooperative inbox adapter lets the existing agent accept a request and
   reply through a tool or CLI. The complete answer is exactly the text supplied
   in its explicit final reply; there is no inference from terminal silence.
3. A terminal adapter may deliver a visible question only if it can establish a
   safe prompt boundary and serialize delivery with all other input writers.
   Deliver a broker-controlled envelope with the sender and request ID, and use
   the cooperative reply mechanism. Input delivery and answer collection are
   separate operations.

The current raw PTY write methods do not satisfy option 3. Checking an `idle`
badge and then calling `write_input` leaves a race with user input and shell
prompts. Bracketed paste helps transport text but does not establish that an
agent, rather than a shell or permission dialog, will receive it. Do not mark a
generic terminal as question-capable until its adapter passes that contract.
Unsupported targets remain discoverable with an explicit capability list.

Responses appear in the existing terminal conversation when handled there,
and the same final response returns to the requester through the API/tool
result. Optional streaming uses explicit sequenced chunks plus a final marker;
v1 can return the final answer atomically. “Complete answer” means the response
to this request, not the recipient's whole transcript or hidden reasoning.

An agent-facing tool surface can be small: `session_self`, `session_publish`,
`session_discover`, `session_ask`, `session_inbox`, and `session_reply`. These
are adapters over the broker, with identical authorization. A CLI should also
support structured JSON and reading replies from stdin/files, avoiding shell
quoting tricks for multiline answers. A blocking `ask` waits on the durable
request; disconnecting the client does not lose or duplicate the question.

## Lifecycle and persistence

```text
queued -> delivering -> accepted -> answered
   |           |           |
   +-----------+-----------+-> declined / expired / cancelled / failed
```

Persist memberships and credential hashes, request content, sender/recipient,
idempotency key and content hash, deadline, delivery attempts, acknowledgment,
answer, and sequenced request events in SQLite. Use transactions and uniqueness
constraints for state transitions and deduplication. Keep this behind a broker
store interface instead of adding request logic throughout `server.py`.

The database is authoritative; EventBus wakes subscribers after commit.
Reconnection replays persisted events, filtered by current authorization.
Never attach a session client to the existing global event stream. Apply the
same check before each emitted event, not just when opening the stream.

There is no exactly-once guarantee for a raw terminal side effect. Persist a
delivery attempt before writing. If the server crashes between terminal write
and acknowledgment, retain an uncertain delivery rather than automatically
pasting it again. Only an adapter with recipient-side deduplication may retry
automatically. A final-answer retry is independently idempotent.

Expired or cancelled requests cannot accept later answers. Cancellation stops
broker delivery and waiting; it does not send Ctrl-C or undo recipient work.
Stopping, deleting, or archiving a recipient terminates its outstanding requests
with a stable reason. A temporarily disconnected recipient can remain queued
until its deadline; discovery must distinguish availability from activity.
Resume creates a new credential generation; fork creates a new session identity
and inherits no active deliveries or credentials.

Prevent unbounded accumulation: proposed initial limits are a 16 KiB question,
256 KiB final answer, 20 pending requests per sender and recipient, 10 new
questions per sender per minute, and a maximum 15-minute deadline. Oversized
answers fail explicitly, never truncate silently. Retain terminal request
records for seven days; retain deduplication tombstones for that same retry
window, with expired replay cursors reported explicitly. Make limits visible
to clients and configurable by the owner.

Reject self-questions. The client must not recursively block both sides on
each other: expose waiting dependencies, reject request cycles when identified,
and retain deadline enforcement even if clients omit dependency information.
Do not make an agent answer a question by creating an unrestricted child agent.

## Authentication and what “no permission” guarantees

Mint revocable, per-session credentials with a launch generation. Store only
hashes, supply credentials through the launch environment or a protected
capability channel, and never put them in URLs, discovery payloads, terminal
envelopes, or request events. Resumed and already-running sessions need an
explicit bootstrap flow; a caller-supplied `DUCKTERM_SESSION_KEY` is not proof
of identity. Existing sessions without enrollment remain owner-visible only.

The owner/control API grants roots and manages enrollment. An agent credential
cannot access owner operations, other sessions' credentials, global discovery,
global events, terminal input, filesystem reads, or approval decisions. Hooks
must migrate to credentials bound to their own session, with narrowly scoped
event-ingest and own-approval capabilities. They must stop reading the global
administrative token. Internal summarizer subprocesses must not inherit usable
session credentials.

Two guarantees must be distinguished:

- **API authorization:** the broker rejects operations outside the session's
  grant, even if the caller supplies another ID, alias, folder, or replay cursor.
- **Process isolation:** a session cannot bypass the broker by reading another
  session's transcript, the SQLite database, the installation token, a tmux
  socket, or the owner API directly.

The first is achievable in this application. The second is not supplied by the
current same-user terminal architecture. A hostile process running with the
owner's filesystem and network privileges can bypass an API filter. Strict
isolation requires an enforced process sandbox/OS identity or container with
restricted mounts and network access to only its scoped broker transport.
Running that sandbox is an additional product capability, not something a
bearer token or a filesystem path comparison can substitute for.

Before advertising strict isolation, close global unauthenticated reads,
protect control-plane credentials and storage from session processes, and
prevent sessions from reaching the control listener. Authenticating global
GETs alone is insufficient while the same process can read the owner token.
Document whether a launch enforces process isolation or only cooperative API
scope. Do not describe the latter as a security sandbox.

Question content is attributed peer input, with no authority to change the
recipient's tools or permissions. Keep it out of system instructions. Owner
visibility should include who asked whom, request status, and the returned
answer; this does not grant other sessions visibility into that audit trail.

## Implementation sequence and release gates

1. **Scope and store:** migrate sidebar folders to stable IDs; add root grants,
   published metadata, broker credentials, and durable requests. Preserve
   existing folder rendering and rename/move behavior through a compatibility
   projection. Revoke grants transactionally on boundary-crossing moves.
2. **Authenticated broker:** implement the versioned API, paginated discovery,
   explicit inbox/answer transport, limits, deadlines, restart recovery, and
   per-request audit events. Separate agent and owner authorization before
   exposing this surface to agent tools.
3. **Usable runtime integration:** enroll new and existing sessions; supply CLI
   and tools; implement and verify one runtime end to end. The receiving agent
   must see the question in its existing session and the requester must receive
   that exact complete answer. Advertise unsupported runtimes accurately.
4. **Terminal delivery:** implement a verified runtime-specific prompt boundary
   and shared input serialization. Test native terminal, supervised PTY, and
   tmux restart paths separately; supported launch modes must be explicit.
5. **Strict containment, if required:** isolate session processes from the owner
   listener, storage, credentials, transcript files, and tmux control. This is
   required before claiming sessions cannot access anything outside their tree.

Required behavior tests:

- Same folder, parent, grandparent, and disallowed ancestor discovery; separate
  roots; ungrouped sessions; sibling prefixes; alias collision and rename.
- Boundary changes between enqueue and delivery, and between completion and
  result retrieval; active streams lose access immediately after revocation.
- Forged sender identity, wrong recipient replies, expired credentials, old
  launch generations, and attempts to use agent credentials on owner routes.
- A asks B, B sees the attributed question, B explicitly replies with a long
  multiline answer, and only A/B and the owner can retrieve that exact answer.
- Busy agents, open approvals, partially typed user input, agent exit to shell,
  unsupported delivery, two simultaneous senders, and reciprocal questions.
- Duplicate creation/reply; crash before and after delivery/acknowledgment;
  disconnect/replay; expiry; cancellation; stop/archive/delete; slow clients.
- Storage bounds, rate limits, oversized payloads, and no credentials or private
  transcript fields in discovery, errors, event streams, or logs.

The first end-to-end milestone is two enrolled sessions in one granted tree
exchanging an explicitly correlated answer, with an unrelated third session
invisible. Raw keystroke injection plus scraping the next terminal output does
not meet that milestone.
