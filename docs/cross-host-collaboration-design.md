# Cross-host session discovery and messaging — design

Status: designed 2026-09-28 at the owner's request (relayed by `main-dev`).
Not implemented. Design and roadmap only; no project-management UI.

Reported symptom: **local `sotto` cannot discover `sotto-remote`**, even
though remote sessions appear beside local ones in the unified Mac app.

## Why it doesn't work today, precisely

Three facts from the code, and together they explain the whole thing:

1. **`/peers` is scoped to one server's database.** `SessionAPI._peer`
   resolves both sender and recipient from `session_api_members` *in the
   local SQLite*, and refuses unless `source["root"] == target["root"]`.
   A session on another machine has no row in that table, so it is not
   "denied" — it is invisible.
2. **The Mac app is the only thing that spans hosts.** `hostTransport.ts`
   proxies requests through `destinationRequest(host, "session-request")`,
   i.e. the desktop app's own SSH transport. There is no server-to-server
   path at all; the two DuckTerm servers have never spoken to each other.
3. **The boundary is deliberate.** `hostTransport.ts` states it outright:
   *"Desktop grouping is presentation metadata. It must not change remote
   inbox authorization."* Sessions look co-located because the app draws
   them together, and that visual placement was explicitly prevented from
   granting access.

So this is not a bug to fix. It is a **capability that was deliberately not
built**, and the owner is now asking for it. The design's job is to add it
without silently converting "drawn in the same list" into "can message each
other".

## The core decision: who carries the message

Three options. I recommend the second.

### A. Server-to-server federation
Each DuckTerm server exposes an authenticated peer API; servers exchange
directly. Clean conceptually, but it makes every laptop a network service
with inbound reachability, TLS, and identity management — a direct
contradiction of the loopback-only trust model that the entire security
review is built on, and a large new attack surface for a single-user tool.
**Rejected.**

### B. Desktop-relayed, servers stay loopback (recommended)
The Mac app already holds authenticated SSH transports to every configured
host and already proxies session requests. Extend exactly that: the app
carries collaboration traffic between hosts, and each server keeps binding
loopback only.
- **No new network exposure.** Nothing becomes reachable that is not already
  reachable, and `gcloud`/SSH keeps owning authentication — the same
  reasoning behind connectors riding existing CLI logins and `backup --to
  gs://` riding the user's own gcloud auth.
- **It matches where the trust already lives**: the owner authenticated
  those hosts; the app is the owner's agent.
- **The honest cost**: collaboration across hosts only works while the app
  is running, and it is not a headless path. That is acceptable for v1 —
  the feature exists so the owner's sessions can talk while the owner is
  working — and it should be stated in the UI rather than discovered.

### C. Relay-mediated (via the sharing relay)
The `share.duckterm.com` relay from
[session-sharing-design.md](session-sharing-design.md) could carry it.
Correct eventually — it is the only option that works with the laptop
closed — but it requires the relay to exist, and it puts inter-agent traffic
through a third party for a case where both machines are the owner's.
**Deferred**, and worth revisiting when the relay ships.

## Identity: host-qualified, never bare

Session ids are unique per host but **not across hosts** — `hostTransport`
already solves this for the UI with `~remote~<hex host>~<hex key>`, noting
"equal session IDs on two hosts stay distinct". Collaboration must use the
same shape rather than inventing a second one:

- A peer is `(host_id, session_key)`. The CLI shows `sotto@this-mac` and
  `sotto-remote@duckterm-dev`.
- `duckterm session ask` accepts a host-qualified id; a bare id stays
  local-only, so **no existing command changes meaning**.
- The host id comes from the app's configured targets, not from a
  self-declared hostname — a session must not be able to claim to be on
  another host.

## Trust: an explicit team, not visual placement

The failure mode to avoid is the one `hostTransport` already warns about:
dragging a remote session into a sidebar folder must not grant it access.

- **Cross-host collaboration is off by default.**
- The owner creates a **team**: an explicit, owner-authenticated grant that
  says "sessions in folder F on host A may collaborate with sessions in
  folder G on host B". Stored on **both** servers, because each must be
  able to refuse independently — a grant that lives on one side is a grant
  the other side cannot revoke.
- **Revocation is immediate and local**: either host can drop its half and
  the pairing dies, with in-flight exchanges cancelled exactly as
  folder-move scope loss does today.
- Placement in the unified sidebar continues to mean nothing for
  authorization. Keep that sentence in the code.

## Routing and delivery

- The app resolves a host-qualified recipient to a target, then POSTs the
  message through the existing `session-request` proxy to that host's
  `/api/v1/session` routes. The receiving server validates the **team
  grant** and the sender's host-qualified identity before accepting.
- **The sender's own server records the message first**, then attempts
  delivery. So an undeliverable message (host offline, app closed) is
  queued rather than lost, and retried when the host reappears.
- **Idempotency already exists and must be reused** — requests carry an
  Idempotency-Key, so a retry after a half-delivered send is safe. Do not
  invent a second dedup scheme.
- **Every cross-host message is marked as such** in the inbox, with its
  origin host. A message from another machine should never be
  indistinguishable from a local one.

## Discovery

`duckterm session discover` gains remote peers **only** for hosts with an
active team grant. Same pagination and 50-record page as today. Each entry
carries its host. A host that is unreachable is reported as
**unreachable, not absent** — the RETRO rule about unsupported being
visible rather than indistinguishable from no-data applies directly: a peer
that has gone quiet because the laptop closed must not look like a peer that
does not exist.

## The concrete `sotto → sotto-remote` flow

1. Owner runs `duckterm team link --host duckterm-dev --folder Duckterm`
   (or the Settings equivalent), authenticated with the owner token on both
   ends via the app's transport.
2. Both servers store the grant.
3. `sotto` runs `duckterm session discover` and now sees
   `sotto-remote@duckterm-dev`, marked remote.
4. `sotto` runs `duckterm session ask sotto-remote@duckterm-dev "…"`. Its
   local server records the request, the app proxies it, the remote server
   validates the grant and files it in `sotto-remote`'s inbox, marked as
   originating from `this-mac`.
5. `sotto-remote` replies normally. The reply travels back the same way and
   lands in `sotto`'s existing thread.
6. If the app is closed mid-flight, the message sits queued and delivers on
   reconnect; the sender sees `queued (host unreachable)` rather than
   silence.

## Phasing

1. **Team grants + host-qualified identity + discovery.** Read-only value
   immediately: the owner can see the whole fleet from either side.
2. **Messaging with store-and-forward**, reusing idempotency keys.
3. **Unreachable-state surfacing** in CLI and inbox.
4. *Later, if the relay ships*: option C as a headless path, so
   collaboration survives the app being closed.

## Acceptance tests

- A session cannot discover a peer on another host **without** a grant, and
  the failure is "not available", not a leak of existence.
- Dragging a remote session into a local folder grants **nothing**.
- Revoking on either host immediately stops discovery and cancels pending
  exchanges on both.
- A message sent while the target host is offline is queued, delivered on
  reconnect, and **not duplicated** if the sender retries.
- Two sessions with the **same session id on different hosts** remain
  distinct throughout discovery, messaging, and the inbox.
- A cross-host message is visibly marked with its origin host.
- Existing single-host behavior is byte-identical: a bare session id still
  resolves locally and no current command changes meaning.

## Owner decisions

1. **App-required is acceptable for v1 — DECIDED 2026-09-28.** The owner:
   "lgtm, dont mind mac app open for local to remote messaging." So the
   desktop-relayed architecture is approved: both servers stay loopback-only
   and the Mac app carries cross-host traffic over the SSH transport it
   already holds. The relay remains the later headless path, not a blocker.
   **What this obliges the UI to say:** cross-host messages queue while the
   app is closed. A sender must see `queued (host unreachable)` rather than
   silence — this project has already been bitten once by a message that
   sat unsent and looked delivered.
2. **Grant granularity**: folder-to-folder, or host-to-host? Recommend
   folder-to-folder, matching the existing scope model.
3. **Should a remote peer's card be visible pre-grant** (so the owner knows
   what to link), or strictly invisible? Recommend invisible, consistent
   with the current "not available" behavior.
