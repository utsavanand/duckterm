# Accounts and session handoff — design

Status: designed 2026-09-22, **not scheduled**. Owner asked for the design
now, implementation "next time". Companion to
[session-sharing-design.md](session-sharing-design.md), which already settles
**live** sharing (relay-first, v4). This doc covers the two things that
design does not: (1) user accounts that own data, and (2) point-in-time
handoff sharing — "take my session from here, with full context".

## Why this is the biggest change proposed so far

Everything shipped rests on one assumption: **one machine, one human, the
loopback is the boundary.** GETs are loopback-gated, POSTs owner-token-gated,
SQLite has a single writer, and [architecture.md](architecture.md) states
plainly that authorization is API-level, not an OS sandbox — acceptable
precisely because there is only one user.

Accounts break that assumption. The moment data is "owned by user X" and a
second user exists, every existing route needs to answer a question it has
never been asked: *whose data is this, and is the caller allowed to see it?*
That is not a feature bolted on the side; it is a property every query must
acquire. Sequencing below is built around that fact.

## Part 1 — Accounts

### The decision that shapes everything: local-first stays

DuckTerm is a downloaded app that runs the user's own agent CLIs on their
own machine. Accounts must **not** turn it into a hosted product with a
mandatory login. Concretely:

- **A logged-out install keeps working exactly as today.** No account, no
  network, no degradation. This is the default and must stay the default.
- An account is what you acquire when you want something that inherently
  needs identity: sharing with another human, or reaching your sessions from
  a second machine.
- The local SQLite remains the system of record for local sessions. An
  account does not move data to a server (see "what we do NOT build").

### Identity provider: GitHub OAuth, one provider

Every user of this tool has a GitHub account, it needs no email
infrastructure, and the sharing design already names it as the v2 identity
option for write grants. One provider, no password storage, no reset flow,
no email deliverability problem. Email magic-link is the documented
fallback if a non-GitHub audience ever appears.

The relay (already designed, already the one service we run) is the natural
place for the OAuth callback — it is the only always-on component. No second
service.

### Schema: one nullable column, not a rewrite

The temptation is a `users` table with foreign keys everywhere. That is the
wrong first move for a local-first app whose local data has exactly one
owner.

- `sessions`, `folders`, and friends gain `owner_user_id TEXT NULL`.
- **NULL means "this machine's local user"** — every existing row, and every
  row created while logged out. No backfill, no migration of meaning.
- A row acquires a non-NULL owner only when it is shared or synced, i.e.
  when a second party could possibly see it.
- A `users` table (id, provider, provider_user_id, handle, avatar,
  created_at) and a `devices` table (the device tokens the sharing design
  already specifies) are the only new tables.

This keeps the single-user path byte-identical and confines multi-user
reasoning to the paths that actually have a second user.

### Authorization: one chokepoint, not N call sites

The failure mode to avoid is scattering `if user_id ==` checks across
hundreds of route handlers — that is how tools grow holes. Instead:

- A single resolver at dispatch turns a request into a **principal**:
  `LocalOwner` (loopback + owner token, today's path), `User(id)` (a relay-
  authenticated account), or `ShareCapability(share_id, mode)` (a link).
- Every data accessor takes the principal and filters. Routes never compare
  ids themselves.
- Default deny for anything that is not `LocalOwner` — a new route is
  invisible to remote principals until someone deliberately opens it.

### What we do NOT build

- **No server-side storage of user data.** Transcripts, database, and code
  stay on the user's machine. The account answers "who are you", nothing
  more. This is the property that keeps the trust story simple and the
  running cost flat.
- **No mandatory login, no free/paid tiers, no org/team model, no RBAC.**
  A share is between two humans until evidence says otherwise.
- **No password auth, no session cookies in the local server.** The local
  server keeps its existing owner-token model unchanged.

## Part 2 — Handoff sharing (point-in-time, "from here on")

The owner's framing: *share my session at a certain point; the recipient has
full context from that point, and continues independently.*

This is **not** the live relay case, and conflating them would be a design
error. It is closer to `git clone` than to screen sharing: a copy with
history, diverging immediately.

### What "full context" actually consists of

Verified against what the machine holds today:

| Piece | Where it lives | In the bundle? |
| --- | --- | --- |
| Conversation transcript | `~/.claude/projects/…`, `~/.codex/…` | **Yes** — this is the context |
| Session metadata (name, intention, folder) | SQLite `sessions` | Yes |
| Checkpoints / digests | SQLite + `~/.duckterm/checkpoints` | Yes |
| Working tree / uncommitted edits | the project directory | **Optional**, opt-in per share |
| Git history | the repo | No — recipient clones the remote |
| Credentials, tokens, connector secrets | keychain, `session-credentials/` | **Never** |
| Live PTY / tmux process | the machine | No — impossible by definition |

The backup code already assembles almost exactly this set with the right
exclusions (`persistence/backup.py`, [backups.md](backups.md)). **Handoff is
a scoped backup of one session plus a manifest** — reuse it rather than
writing a second archiver.

### The flow

1. **Owner**: "Share a copy of this session" → picks whether to include
   uncommitted working-tree changes → gets a link.
2. **Bundle**: one archive, scoped to that session, encrypted client-side
   with a key in the URL fragment (the relay never sees plaintext — the same
   fragment-key trick the sharing design specifies for live shares).
3. **Relay**: stores the blob, addressed by path, default 7-day expiry,
   revocable, size-capped. It is a dumb store; it cannot read the contents.
4. **Recipient**: authenticated with a GitHub account (a handoff carries
   conversation history — this is not a "link is the credential" case),
   downloads, and DuckTerm reconstructs a **new local session** with the
   transcript in place, resumable so the agent continues with full context.
5. From that moment the two sessions are independent. **No sync, ever.**

### The disclosure problem, stated plainly

A transcript contains everything the agent saw: file contents, error
messages, and anything the owner pasted — including secrets pasted into a
conversation, which `backups.md` already warns are not scrubbed. Sharing a
session is therefore a **disclosure act**, not a convenience.

Therefore, non-negotiable in v1:

- A **preview before sending**: what is included, how large, how many
  conversation turns, which files appear in the working-tree portion.
- The confirmation names the recipient. No public/anonymous handoff links.
- Expiry and revocation by default; revocation is meaningful only before
  download, and the UI must say exactly that rather than implying recall.
- Never bundle credentials (reuse the backup exclusion list, which is
  already tested).

### Resumability is per-harness, and partly unknown

Claude Code supports conversation forks today (the dashboard already forks
conversations), which is strong evidence a transplanted transcript resumes.
Codex and Copilot are unverified. Treat "can a transcript be resumed on
another machine" as a **capability on the Harness contract**, default
unsupported, and prove it per harness with a real cross-machine test before
claiming support. A handoff that lands as a read-only archive is still
useful; silently claiming resumability that does not work is not.

## Part 3 — Live sharing (already designed)

[session-sharing-design.md](session-sharing-design.md) v4 settles this: a
small path-addressed relay, laptop dials outbound, viewers watch read-only
at `…/s/<id>#<token>`, presence, and a scoped-ask channel; v2 adds
owner-approved prompt proposals; v3 handoff of long-running sessions.

Two corrections this doc makes to it:

1. **The owner's constraint — "live sharing only when the session runs
   remotely on a box" — is stricter than the relay design assumes.** The
   relay works fine from a laptop, but a laptop that sleeps kills the share.
   Requiring (or strongly steering toward) sessions on the remote workspace
   VM makes live sharing actually dependable. Live sharing therefore
   **depends on the remote workspace landing** (roadmap item 3), not just
   on the relay.
2. Accounts (Part 1) supply the identity layer that design defers to "v2,
   before granting write". Build accounts before any write-capable share.

## Sequencing

Nothing here is scheduled; this is the order when it is.

1. **Accounts, logged-out-still-works** — GitHub OAuth on the relay,
   `users`/`devices` tables, nullable `owner_user_id`, the principal
   resolver with default-deny. Ships with zero user-visible change for a
   logged-out install; that is the acceptance test.
2. **Handoff sharing** — scoped-backup bundle + encrypted blob on the relay
   + reconstruct-locally. Delivers real value without any live
   infrastructure, and exercises accounts for the first time.
3. **Live sharing v1 (watch)** — after the remote workspace lands, per the
   dependency above.
4. **Write-capable sharing** — only after 1–3 are real, and only as
   "propose a prompt, owner approves", never raw keystrokes to a PTY.

## Open questions for the owner

- Is a hosted/multi-tenant product ever the goal? If yes, the "no
  server-side user data" constraint above is the thing to revisit first —
  and it would change nearly every decision here.
- Handoff recipients: only people with a GitHub account, or is an
  email-link fallback needed for non-developers?
- Should a handoff bundle default to including uncommitted working-tree
  changes, or default to excluding them? (This doc assumes opt-in.)
