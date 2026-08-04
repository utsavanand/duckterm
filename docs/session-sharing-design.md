# Session sharing (multiplayer) — design

Status: DRAFT v2 — research done, proposal written, validation loops pending.

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

### v1 — "watch + ask" over a relay (build now)

The smallest thing that is genuinely multiplayer (live, not a transcript
export) and whose parts all survive into later phases.

**New component: `relay` — one small service, deliberately dumb.**
- Accepts one outbound WebSocket per RubberTerm install ("device socket"),
  authenticated by a device token minted at first connect.
- `POST /shares` from the device: `{session_key, mode: "view", ttl}` →
  `{share_id, url}`. Share row: `{share_id, device_id, session_key, mode,
  exp, revoked}` in SQLite on the relay.
- Serves the share page (static: xterm.js viewer + ask box) at
  `/s/<share_id>#<token>` — token in the URL fragment so it never appears in
  server logs or Referer headers; the page presents it over the WS.
- Fans terminal frames from the device socket out to viewer sockets; forwards
  `ask` messages down to the device and returns the answer.
- Host offline → keeps the last screen, shows a banner with the disconnect
  time; rejects asks. Device reconnects with backoff (same pattern as the
  browser terminal client).
- Deploy: single Fly.io shared-cpu app (~$5/mo). Python, same hand-rolled
  HTTP/WS transport the main server uses — no new framework.

**Local server changes.**
- A device-socket client task (connect out, re-register live shares on
  reconnect, answer `ask`, stream the shared session's byte feed).
- `POST /sessions/:key/share` + revoke + list, and the share UI in the
  session detail panel (create link, copy, see viewers, revoke).
- Join/leave notifications surface like approvals do today.
- Read-only is structural in v1: the device never accepts input frames from
  the relay at all — there is no code path from a viewer to a PTY fd.

**What a recipient gets from the emailed link:** the live terminal
(read-only), the session's name/state/goal, and an ask box answered by the
owner's machine via the existing `/fleet/ask` digest, scoped to that session.
Owner's email client sends the link; we build no email infra in v1.

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
- Local enforcement: every frame from the relay carries the share_id; the
  local server checks mode + grant before acting. The relay checking too is
  defense in depth, not the boundary.

### v3 — sessions that outlive the laptop (build when handoff demand is real)

- The same server image runs on a $6/mo Fly machine/VPS and dials the same
  relay as just another device. A "handoff" moves a session's repo state
  (branch push + conversation export/resume) from laptop-device to
  cloud-device. No E2B/Modal-style sandboxes — wrong pricing and lifecycle
  for days-long tmux sessions.
- This is also where accounts/teams/pricing enter (the market monetizes
  exactly here). Not designed further now.

### Explicitly rejected

- **Tunnels (cloudflared/Funnel) as the product mechanism** — URL rotation
  breaks emailed links, whole-surface exposure, nothing carries forward.
  (Still fine for personal one-off use by power users.)
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

(to be run: architecture/over-engineering critique, security review of the
share path, failure-mode walkthrough — findings and resulting changes will be
recorded here.)

## Open questions for the owner

1. Hosted relay = first always-on infra we operate, and the natural control
   plane for a future paid tier. Comfortable running that (~$5/mo, one small
   app), or should v1 ship relay-self-host instructions alongside?
2. Positioning: is sharing the start of the paid product (market convention),
   or free while single-user features stay the product?
3. Does v1's read-only + ask meet the YC "multiplayer" bar you want to demo,
   or should v2's request-control flow be pulled into the first release?
