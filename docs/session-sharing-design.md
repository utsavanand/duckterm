# Session sharing (multiplayer) — design

Status: DRAFT v3 — research + four validation loops done; proposal revised
below. The headline change from v2: **v1 drops the relay for a tunnelled
read-only viewer**, and **"ask" must be rebuilt scoped to one session** (the
current `/fleet/ask` digests every session — a real leak, not a design nit).

## The ask

Share a running RubberTerm session with another person over email so they can:

1. **View** the live terminal (read-only), from any browser, no install.
2. **Ask questions** about the session (the existing `/fleet/ask` digest
   mechanism, scoped to the shared session).
3. **Prompt / drive** the session — only when the owner explicitly grants it.

This is the "multiplayer AI" direction (YC F2026 RFS). It is also the first
feature that can't be served from 127.0.0.1 — someone else's browser has to
reach the session.

## Constraints that shape the design

- RubberTerm today is localhost-first: one asyncio server on the owner's Mac,
  loopback-gated GETs, token-gated POSTs, tmux-backed PTYs. That server is
  already the host-side anchor every sharing tool has to invent.
- Terminal input = arbitrary code execution on the owner's machine. Write
  access is a security boundary, not a UI toggle: it must be enforced
  server-side per connection.
- The owner's laptop sleeps. Whatever we build must fail loudly and obviously
  when the host is gone, or the session must live somewhere that doesn't sleep.
- Solo developer, no infra team. Every always-on component we add is a pager
  we now carry.

## Research findings

### YC's "Multiplayer AI" RFS (Fall 2026)

Aaron Epstein's RFS (ycombinator.com/rfs#multiplayer-ai) names the exact gap:
"When you want to collaborate with your teammates and agents, the best you can
do is send a link to a read-only transcript they can't touch. … Anyone on a
team should be able to drop into the same live agent session to watch it work,
redirect it, and hand it off." The read-only transcript link is explicitly the
status quo to beat; the bar is watch → redirect → hand off, live.

### Prior art: terminal sharing (tmate, Upterm, sshx, Live Share, Warp)

Three patterns repeat across every tool that shares a terminal:

**A — Outbound rendezvous relay.** The host always dials OUT; nobody dials
into the host. tmate (SSH out to relay), Upterm (reverse tunnel; the real
session still terminates on the host), sshx (gRPC out; relay is a blind
forwarder of E2E-encrypted frames), VS Code Live Share (P2P first, relay
fallback, E2E on top), cloudflared/ngrok (tunnels). Trust in the relay varies
from "sees everything" (tmate, Warp) to "sees ciphertext only" (sshx, Live
Share).

**B — Capability URLs; two capabilities for two permission levels.** The
unguessable link IS the credential, and read-only vs read-write are two
different secrets enforced at the relay or host — never client-side. tmate
mints separate rw/ro tokens (150-bit); sshx mints a second reader URL with the
key in the URL fragment (never sent to the server); Live Share layers identity
(GitHub/Microsoft sign-in + host approval) on top when stakes rise. Universal:
write is granted, not defaulted — read-only default, "request control" flows,
join notifications, eject.

**C — Host-anchored vs cloud-published lifecycle.** tmate/Upterm/sshx/Live
Share: host offline → session dead, nothing persisted (good privacy story).
Warp publishes the session to its cloud: survives the host, enables async
review, vendor sees everything. A deliberate product choice, not an
implementation detail.

### Prior art: agent products

- **The unit of sharing is a session URL** everywhere: Devin session URLs
  (org-visible, teammates can message the running session), Cursor agent-run
  URLs (repo-permission-verified viewers; "team follow-ups" upgrades them to
  steering), Conductor share links → "early access multiplayer" → Conductor
  Cloud (July 2026, $50/mo Pro tier), Claude Code's per-session visibility
  toggle (Team = org-visible read-only snapshots), Codex task links, Copilot
  coding-agent session pages (anyone with repo access can watch AND steer).
- **Authorization convention is repo access, not email possession** — Cursor,
  Claude Code Team, Devin, Copilot all gate on the recipient's GitHub repo
  permissions. Email/Slack is the notification channel; GitHub identity is
  the authorization channel. Nobody ships bare magic-link sharing for code
  sessions.
- **Products with real multiplayer moved execution to cloud VMs first**
  (Devin, Cursor, Codex, Claude Code web, Copilot, Conductor Cloud). The one
  proof that the relay-from-your-machine model works is **Omnara**: execution
  stays local, a cloud relay (SSE + persisted transcript + push notifications)
  provides remote view and control from web/mobile — but single-user only;
  they never shipped teammate sharing. Cautionary tales: Omnara's v1
  terminal-parsing wrapper was abandoned as unmaintainable (RubberTerm's
  hook-based integration doesn't have that fragility); Terragon (pure cloud)
  shut down Jan 2026.
- **Sharing is the paid tier** across the market: Conductor Pro $50/mo, Devin
  Teams $80+$40/seat, Omnara Enterprise, Claude Code Team plans.
- **Slack threads are the de-facto team surface** (Cursor, Devin,
  Claude-in-Slack, Codex) — worth treating as a future notification/steering
  channel, not v1.

### Architecture options evaluated

Four architectures, evaluated for a solo developer:

**1. Relay (host dials out).** One tiny cloud app (Fly.io shared-cpu-1x
$2–6/mo, or a Cloudflare Durable Object with WebSocket hibernation ~$5/mo
flat). The local server keeps one outbound WebSocket to it, authenticated by a
device token. Share creation registers `{share_id, session_id, mode, exp}` at
the relay; viewers' browsers connect to the relay, which fans terminal output
out and forwards input down the device socket only for `interact` shares.
This is tmate/Upterm/sshx's shape minus the SSH protocol (our client is a
browser). `/fleet/ask` proxies as a request/response pair over the same
socket. Laptop sleeps → relay serves the last snapshot with a "host offline"
banner; local reconnects with backoff. Effort M (2–4 days for read-only +
ask). Everything built here (share table, capability tokens, device protocol)
carries into every later phase.

**2. Tunnel-as-a-feature (cloudflared / Tailscale Funnel).** Shell out to
`cloudflared tunnel --url localhost:4300` per share. Effort S, cost $0 — but a
dead end: Quick Tunnel URLs rotate on every restart (breaking emailed links),
long-lived WS through quick tunnels is flaky and explicitly not production,
Funnel needs the non-App-Store tailscaled on macOS, and it exposes the ENTIRE
local HTTP surface — one missed auth check on any endpoint is remote code
execution on the owner's Mac. Nothing built here is reusable.

**3. Cloud-run sessions.** Run the same server + tmux + agent on a cloud box;
sharing becomes ordinary multi-user auth on a host that never sleeps. A plain
$5–6/mo Fly machine/VPS beats agent-sandbox platforms ~10x for this shape:
E2B/Daytona ≈ $72/mo always-on with 24h session caps; Modal is priced and
lifecycled for burst execution, not days-long interactive tmux. Effort L —
not the port (the server is portable) but auth, secrets, repo sync, and the
product shift away from localhost-first.

**4. Async-first (sync snapshots to storage, no live connection).** Upload
terminal snapshots/asciicast streams to R2 every few seconds; share links
serve a near-live replay; prompts queue for the local server to poll. ~$0/mo,
but 5–30s staleness, interactivity effectively impossible, and a laptop
polling a queue is just a worse persistent WebSocket. One idea worth keeping:
asciicast-format session recording for permanent replays, orthogonal to
sharing.

**Auth for recipients.** v1: the link is the credential — per-link capability
tokens (`share_id.exp.HMAC(secret, share_id|mode|exp)`), a share table with a
revocation flag, default 7-day expiry. No accounts, no email infra, ~50
lines. v2 (before granting write): verified identity — either email
magic-link or GitHub OAuth with a repo-access check (the market convention).
The capability stays in our share table either way; the identity layer only
answers "who is this."

**PTY security.** Interact = arbitrary code execution on the host. Read-only
must be enforced at BOTH ends (relay drops input frames; the local server
also checks the connection's capability before writing to the PTY fd —
defense in depth if the relay is compromised). Short expiries, visible viewer
list + join notifications, per-share kill switch, input audit log. Strongly
prefer granting "send a prompt to the agent" (a structured message into the
agent's input flow) over raw keystrokes — smaller blast radius, and it's what
a collaborator actually wants.

## Proposal

The staging below is the post-validation version. The v2 design named a relay
"build now" and reused `/fleet/ask`; both over-engineering reviews and the
security review pushed back, and they were right — see Validation loops.

### v1 — "watch + scoped-ask" over a tunnel (build now)

The smallest thing that is genuinely multiplayer (live, not a transcript
export) and whose only always-on dependency is one the owner already runs. No
relay, no hosted service, no pager.

**Local server changes.**
- A dedicated **viewer listener** on its own port that serves exactly two
  things: a static xterm.js share page, and a read-only byte-stream WS for one
  shared session. It has NO input path — no keystroke, resize, or control
  handler reaches a PTY. This is the structural read-only boundary; it reuses
  `subscribe_bytes` (which already sends a clear-screen `\x1b[2J\x1b[H` +
  capture snapshot on attach) minus the input half. Read-only is enforced by
  the absence of code, not a client toggle.
- A **scoped ask endpoint** on that listener: `ask(session_key)` digests ONLY
  the shared session and answers via the summarizer. This is a NEW code path —
  the existing `/fleet/ask` digests every running session (`self.history.
  sessions()`), so reusing it would leak every other session to a viewer.
  Rate-limited and owner-budgeted (see security findings F1).
- A **share table** in the local SQLite: `{share_id, session_key, token_hash,
  mode, exp, revoked}`. `share_id` and `token` are per-share random (≥128-bit);
  mode lives in the row, never in the token (F2). Revoke flips the flag AND
  drops any live viewer socket for that share within seconds (F7).
- `POST /sessions/:key/share` (+ revoke, list) on the main server, and a share
  panel in the session detail drawer (create link, copy, see viewer count,
  revoke). Join/leave surfaces like approvals do.

**Exposure.** The owner points a **stable named cloudflared tunnel** (not a
Quick Tunnel — those rotate URLs and break emailed links) at the viewer
listener's port only. Because the tunnel fronts a listener that serves only
the read-only stream + scoped ask, the "whole HTTP surface exposed" objection
that killed tunnels-as-mechanism in v2 doesn't apply — the surface IS the
share surface. First-run wires the tunnel; the owner needs a Cloudflare
account + a domain (or we ship a small default).

**What a recipient gets from the emailed link** (`https://<tunnel>/s/<share_
id>#<token>`): the live terminal (read-only), the session's name/state/goal,
and an ask box answered by the owner's machine, scoped to that one session.
The owner's email client sends the link; we build no email infra. Token in the
URL fragment (kept out of request lines/Referer), 24h default TTL, revocable.

**Laptop sleeps** → the viewer's WS drops and the share page shows a "host
offline, reconnecting" banner (the same reconnect-with-backoff the browser
terminal client already implements). No frozen-screenshot persistence — that
was a Warp-style choice the v2 doc adopted without noticing it contradicts the
host-anchored privacy story of every tool it cited (finding from the
over-engineering review).

### When the relay earns its place (v1.5, triggered — not now)

The relay stops being speculative the moment tunnels hit a concrete limit the
owner actually feels: (a) two or more distinct people have watched a share and
wanted the link to survive a server restart / not require the owner's tunnel
running, or (b) fan-out to several simultaneous viewers per share becomes real
(the tunnel + local listener handle "some browsers connected," but a relay's
per-viewer bounded queues and viewer caps are the clean answer at scale). At
that trigger, build the outbound-rendezvous relay (Fly shared-cpu ~$5/mo, the
same hand-rolled transport grown a WS *client* half — note `transport/
websocket.py` is server-only today, so this is real work the v2 estimate hid).
Everything from v1 (share table, capability tokens, scoped ask, structural
read-only) moves behind it unchanged; the local listener becomes a device
socket. Until the trigger, the tunnel is the honest answer.

### v2 — "prompt it" (build when a real user asks for it)

- Grant model: the owner flips a share to `interact` per viewer request
  ("request control" on the share page → notification → owner approves).
- Identity before write: GitHub OAuth on the share page; optionally require
  repo access to the session's repo (the market's authorization convention).
  The grant is stored per identity, not per link.
- The write capability is **"send a prompt"** — a structured message injected
  through the same path the Messages annotation flow already uses — not raw
  keystrokes. Raw-keystroke sharing (full terminal control) stays out until
  there's a concrete need; if added, it gets its own scarier grant, an input
  audit log, and a kill switch.
- Local enforcement: every frame carries the share_id; the local server
  checks mode + grant before acting.
- **"Send a prompt" is RCE-equivalent** (F6): a prompt into an agent with
  shell/file/git tools runs arbitrary commands as the owner. So write requires
  owner **preview-before-inject** (approve each shared prompt, like approvals
  today), a per-share prompt rate limit, and an agent-permission cap while any
  interact-share is live (never `--dangerously-skip-permissions`).
- **Repo-access gating is a filter, not the grant** (F10): verifying GitHub
  repo access bounds *who can ask* to a known, revocable identity — it does
  NOT make a shell on the owner's laptop safe (read access to one repo ≠
  should-have-shell). Explicit per-viewer owner approval stays the boundary;
  don't let an "auto-grant if repo write" shortcut regress it.

### v3 — sessions that outlive the laptop (build when handoff demand is real)

- The same server image runs on a $6/mo Fly machine/VPS and dials the same
  relay as just another device. A "handoff" moves a session's repo state
  (branch push + conversation export/resume) from laptop-device to
  cloud-device. No E2B/Modal-style sandboxes — wrong pricing and lifecycle
  for days-long tmux sessions.
- This is also where accounts/teams/pricing enter (the market monetizes
  exactly here). Not designed further now.

### Explicitly rejected

- **Building the relay in v1** — reversed after validation. Two independent
  over-engineering reviews found the relay was v3's paid-tier control plane
  built speculatively under "everything carries forward," justified by
  objections that apply to Quick Tunnels, not the stable named tunnel v1 now
  uses. It returns as a *triggered* v1.5.
- **Building on Liveblocks/Yjs/CRDT infra** — terminal bytes are a broadcast
  stream, not a merge problem. Presence ("Alice is watching") is a counter,
  not a CRDT.
- **Slack integration as v1** — strong future notification channel, but it
  doesn't remove the need for the relay + share page, and it adds an app
  review + OAuth surface now.
- **E2E encryption of frames in v1** (sshx-style key-in-fragment) — the relay
  is ours and single-tenant-ish at this scale; revisit when third parties run
  relays or if positioning demands "relay can't read sessions."
- **Accounts/sign-in in v1** — capability links with revocation are the
  tmate-proven baseline; identity arrives in v2 where write access makes it
  load-bearing.

## Validation loops

Four adversarial reviews ran against the v2 draft. Their load-bearing findings
and how the proposal changed:

### Over-engineering (two independent reviews, same verdict)

- **Cut the relay from v1.** Its only v1 job — one remote browser reaches one
  session read-only — is done by a stable named tunnel. Fan-out, presence, and
  a stale-frame-on-sleep don't justify standing up the first always-on service
  + pager. Both reviews independently landed here. → **Adopted:** v1 is now
  tunnel-fronted; the relay is triggered v1.5.
- **"Everything carries forward" is speculative future-proofing** (the
  project's own architecture-slop guardrail) — the device socket + hosted
  share table + fan-out is v3's paid-tier control plane. → Deleted as a build
  justification.
- **Host-offline snapshot persistence contradicts the doc's own research** —
  tmate/Upterm/sshx/Live Share all do host-offline → session-dead; only Warp
  persists. → **Adopted:** v1 shows a reconnect banner, no persisted frame.
- **The effort estimate hid a real gap** — `transport/websocket.py` is
  server-only (no client handshake, no outbound frame masking), so the device
  socket needs a WS *client*. → Noted in the v1.5 trigger section.
- Kept as correct simplicity calls: rejecting CRDT/Liveblocks, E2E-in-v1,
  Slack-in-v1, cloud sandboxes; "send a prompt" over raw keystrokes; making
  read-only structural.

### Security (blocking findings — must be true before v1 ships)

- **F1 / "scoped ask" is fictional in the current code** — `/fleet/ask`
  digests *every* running session and its live screen, and a viewer-controlled
  question already deepens the dump for any session it names. Reusing it hands
  a viewer every other session's output and an unbounded `claude -p` invocation
  on the owner's account. → **Adopted:** v1 builds a NEW session-scoped ask,
  rate-limited + owner-budgeted, with the question wrapped as untrusted input;
  regression test that a share for A cannot surface B.
- **F2 — per-share random tokens, mode in the row, not HMAC-with-one-secret**
  (a secret leak would forge every share, incl. future `interact`). → Adopted.
- **F3 — "no input path" must be audited, not asserted:** sever viewer resize
  (a viewer must not drive `TIOCSWINSZ` on the owner's PTY), allowlist the
  viewer→owner message set to exactly `{ask}`, reject+log anything else. →
  Adopted into the structural-read-only definition.
- **F4 — device token lifecycle** (keychain storage, per-device revoke for a
  stolen laptop, rotation) — a v1.5/relay concern, tracked there.
- **F7 — revocation/expiry must drop LIVE viewer sockets**, not just gate at
  connect. → Adopted into the share-table revoke behavior.
- Accepted-with-written-risk: relay sees plaintext (F5, defer E2E, don't
  persist frames), fragment-token residual leak (F6, 24h TTL, no-referrer,
  don't log the auth frame), Origin-check the new local POSTs (F8).

### Failure modes (the new boundary is where silent breakage lives)

- The four that turn "share doesn't work" into an undebuggable dead end, all
  **relay-boundary** concerns → deferred with the relay to v1.5, where they
  become must-fix: reconnect reconciliation (device is source of truth for
  liveness), a `share_ended` signal distinct from "host offline" (else a
  deleted/stopped session shows a false offline banner forever), ask
  correlation-id + timeout, and a protocol version handshake.
- Reusable local primitives confirmed present: bounded byte queues with
  drop-on-full and clear-screen resync (`orchestrator.py`), so the tunnel-v1
  viewer inherits sane backpressure and repaint for free.
- v1 (tunnel) note: add **jitter** to reconnect backoff before the relay
  exists, so a relay redeploy later doesn't cause a synchronized reconnect
  storm.

## Open questions for the owner

1. **Tunnel setup friction.** v1 needs a stable named cloudflared tunnel,
   which means a Cloudflare account + a domain. Fine to ask the owner to set
   that up once, or should we ship a hosted default (which is the relay, one
   phase early)?
2. **Positioning.** Sharing is the paid tier across the whole market
   (Conductor $50/mo, Devin Teams, Claude Code Team). Start the paid product
   here, or keep it free while single-user features stay the product?
3. **Demo bar.** v1's read-only watch + scoped ask is live and multiplayer —
   it clears the YC "not a read-only transcript" line. Is that enough to demo,
   or do you want v2's request-control (write) pulled into the first release
   despite its RCE surface?
4. **Build v1 now, or hold?** The doc is decision-ready. Nothing is built yet.
