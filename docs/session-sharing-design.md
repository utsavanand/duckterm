# Session sharing (multiplayer) — design

Status: DRAFT v4 — a second, deeper research round (relay build-vs-buy, WebRTC
P2P, remote-prompt safety, identity/follower) settled the architecture. The v3
"tunnel, no relay" recommendation is **reversed**: it doesn't survive contact
with a second user. Decision below.

## Decision (v4)

**Build a small relay we run, addressing shares by PATH under one domain.**
The session keeps running on the owner's laptop; the laptop dials the relay
outbound; viewers connect to `https://share.rubberterm.com/s/<id>`. This is
the model tmate/sshx/upterm converged on and it dissolves every objection the
earlier drafts wrestled with:

- **The domain problem that killed the tunnel plan doesn't exist for a relay.**
  Tunnels route by *hostname*, so each sharer seemed to need their own
  subdomain/domain/account (a non-starter for a downloaded app). A relay routes
  by *path* — one domain we own, `…/s/<session-id>`, no per-user DNS, no certs
  to automate, zero user setup. The v3 tunnel idea required every sharer to own
  a domain + Cloudflare account; that was the fatal flaw.
- **A relay exposes only the pushed frames; a tunnel exposes the whole laptop
  server.** With a tunnel we'd have to build auth + session-scoping in front of
  the entire localhost server anyway. The relay inverts it: the laptop pushes
  exactly one session's bytes; nothing else is reachable even in principle.
- **True P2P (WebRTC) was investigated and rejected.** It can't remove the
  central server (signaling needs one; TURN fallback carries *all* traffic for
  the corporate-network teammates who need it most — 30–70% of them). At a
  terminal's KB/s, TURN and a dumb relay cost the same (~$0), so P2P buys no
  bandwidth savings, only complexity (aiortc drags heavy native deps, breaking
  the zero-dependency install). What the owner actually wants from "P2P" — code,
  agent, keys all stay on the laptop — the relay already delivers; the relay
  only ever sees a byte stream. The last 10% ("relay can't even read the bytes")
  is ~200 lines of sshx-style fragment-key encryption, later, not WebRTC.
- **Cost & size:** ~300–500 lines of the same asyncio idiom, flat ~$5/mo on
  Fly.io or a Hetzner box (does not grow with usage). It is not a reverse tunnel
  reinvented — we control both ends and forward one message type.

Runner-up (documented, not built): Cloudflare Tunnel provisioned
programmatically under our own account is genuinely free and viable, but it
still exposes the whole local server and makes us supervise a `cloudflared`
child process on every user's Mac. Revisit only if we ever need to expose
arbitrary local ports (previews), not terminal frames.

## The ask

Share a running RubberTerm session with another person over email so they can:

1. **View** the live terminal (read-only), from any browser, no install.
2. **Ask questions** about the session (a new session-scoped digest — NOT the
   existing `/fleet/ask`, which reads every session; see v1).
3. **Prompt / drive** the session — only when the owner explicitly grants it.

This is the "multiplayer AI" direction (YC F2026 RFS). It is also the first
feature that can't be served from 127.0.0.1 — someone else's browser has to
reach the session.

> YC's RFS, verbatim: *"The best work tools of the last two decades won by
> going multiplayer. Google Docs replaced Microsoft Word. Figma beat Photoshop
> … But AI hasn't had its multiplayer moment yet. … right now, working with AI
> is largely single-player. … the best you can do is send a link to a read-only
> transcript they can't touch. … Anyone on a team should be able to drop into
> the same live agent session to watch it work, redirect it, and hand it off,
> the way they'd work with any other human team member."*
>
> The three verbs map onto the phases below: **watch** = v1, **redirect** = v2,
> **hand off** = v3. The read-only transcript link is explicitly the thing to
> beat — v1's live read-only view already clears that bar.

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

## Proposal (v4, relay-first)

Three phases. Each is the smallest thing that clears its bar; nothing is built
speculatively for the next. Maps to the YC framing — v1 *watch*, v2 *redirect*,
v3 *hand off*.

### The relay (the one piece we run)

A small always-on service under one domain we own. Deliberately dumb:

- Accepts one **outbound** WSS per shared session from the owner's laptop
  (`/agent/<id>` + a device bearer token). Outbound works through every NAT and
  corporate firewall — it's ordinary HTTPS. The laptop pushes exactly the same
  binary xterm frames it already streams to the local browser.
- Serves the viewer page + read-only stream at `https://share.rubberterm.com/s/<id>`.
  **Path-based, not per-user hostname** — one domain, one cert, no DNS
  automation. Fans frames to N viewer sockets; presence and (v2) prompt
  proposals flow back on the same socket.
- State is a dict (`session-id → {agent socket, viewer sockets, presence}`); a
  restart just drops connections and the laptop re-dials with backoff (add
  jitter so a relay redeploy doesn't cause a synchronized reconnect storm).
- ~300–500 lines of asyncio, same idiom as the main server. Deploy: one Fly.io
  shared-cpu (~$3–5/mo) or a Hetzner CX22 (~€4.35/mo). Flat cost; terminal
  bytes are tiny.

### v1 — watch + scoped-ask (build now)

**A recipient opens an emailed link and watches the live terminal, read-only,
and can ask questions about that one session.** Genuinely multiplayer (live,
not a transcript), and the read path has no way to touch the machine.

- **Device→relay auth (ngrok model):** `rubberterm login` mints a 256-bit
  random opaque device token, stored in the macOS keychain (not a dotfile),
  presented as `Authorization: Bearer` on the outbound dial. Relay stores only
  a hash. Revoke = delete the row (the "laptop stolen" recovery path); a device
  list shows name/created/last-seen. No JWTs, no per-session keys — the relay
  is the only verifier, so an opaque token + DB row is simpler and instantly
  revocable.
- **Viewer auth = the link is the capability** (tmate/sshx): share URL is
  `…/s/<id>#<128-bit-token>`. The token rides in the URL **fragment**, which the
  browser never sends in the request line — kept out of relay logs and Referer;
  the page reads it and presents it inside the WS handshake. Per-share token,
  hashed at rest, 24h default TTL, one-session scope, revocable. Leaked/forwarded
  link ⇒ still read-only. Serve the page `Referrer-Policy: no-referrer`, zero
  third-party JS (so nothing can read `location.hash`).
- **Structural read-only:** the viewer stream handler has NO branch that writes
  to a PTY — not a disabled input box, an absent code path. Viewer geometry is
  NOT forwarded to the PTY (a viewer must not drive `TIOCSWINSZ` on the owner's
  terminal); viewers letterbox/scale client-side. The only viewer→laptop message
  in v1 is `ask`. A regression test asserts a viewer-credentialed connection's
  input is dropped server-side.
- **Scoped ask (a NEW endpoint):** digests ONLY the shared session. The existing
  `/fleet/ask` digests every running session (`history.sessions()`) and a
  viewer-controlled question already deepens the dump for any session it names —
  reusing it would leak every other session to a viewer. The scoped version is
  rate-limited and owner-budgeted (a leaked link must not become unbounded
  `claude -p` spend on the owner's account), and wraps the question as untrusted
  input with a "never reveal secrets/keys" system prompt (best-effort; the real
  control is the scoping).
- **Late-join snapshot:** on viewer connect the laptop sends a
  `tmux capture-pane -e` snapshot (clear-screen + paint) then streams — the
  joiner sees the current screen instantly. (Reuses the existing attach path.)
- **Laptop offline:** viewers get a "host offline / session ended" close with
  the right reason (distinguish "device socket dropped" from "session ended" —
  a deleted/stopped session must not show a false "offline, reconnecting"
  banner forever). No persisted frozen screenshot.

### The follower / presence layer (v1)

For a single shared byte stream there is no per-viewer viewport — everyone
renders the same grid, so **everyone already follows the owner, for free**
(Live Share's default-follow mode is our only mode). The one per-viewer axis is
**scrollback**: at the live edge vs. reviewing history. So "follow" for a
terminal is exactly a live/reviewing state, and the presence layer is:

- **Name on join** (viewers type one; write-tier viewers get their verified
  email name). Per-connection, nothing persisted.
- **Viewer list + "N watching"** broadcast as small JSON frames to every
  connection incl. the owner's laptop; the owner's header shows who's watching.
- **Join/leave toasts** + a **macOS notification to the owner on every join** —
  this is both the multiplayer feeling and the anti-silent-watcher control
  (the tmate-as-backdoor lesson).
- **"● LIVE / reviewing" indicator**; scrolling up flips a viewer to "reviewing,"
  broadcast so the owner sees "B is reviewing scrollback" vs "live."
- Wire format reuses the existing WS: binary frames stay terminal bytes,
  presence is text JSON, heartbeat piggybacks on the planned ping/pong. No CRDT,
  no persistence — plain broadcast presence (Liveblocks/Yjs-awareness semantics).
- Deferred (no coordinate space in a terminal): named 2D cursors, per-follower
  shared-viewport control, Figma-style spotlight, chat.

### v2 — redirect it: propose-a-prompt, owner-approved (build when asked)

**The design rule: B never gets a keyboard — B gets a suggestion box.** A prompt
into an agent with shell/file/git tools is arbitrary code execution as the
owner, so the write primitive is a *proposal the owner approves*, injected by
RubberTerm's own code on the owner's Mac. This is stricter than Omnara (which
types remote text straight into stdin, and has no notion of a second person)
and mirrors Live Share (identity → host approval → per-resource grant →
host-side enforcement → host can always see/intervene/eject).

Must be true before write-sharing ships:

1. **Write capability never travels in the link.** A leaked URL can watch,
   never type. (Test it — the tmate/sshx cautionary tale is a read-only
   credential that was silently a write credential.)
2. **Authenticated identity before write is requestable.** Anonymous viewers
   can't request write. Recommendation: **email magic link**, not GitHub OAuth —
   the invite went out over email, so "prove you control that inbox" is the
   identity proof that matches the channel; GitHub proves a different identity
   that may not map to the person. Roll-your-own (one `login_tokens` table +
   Resend/SES, ~free) or Supabase Auth free tier. Repo-access is only ever a
   *filter* on who-may-ask, never authorization to have a shell.
3. **Request/grant ceremony, per person per session.** B clicks "Request to send
   prompts" → owner sees "Bala (b@corp.com) wants to send prompts to session
   foo [Allow/Deny]" (reuse the existing approvals UI). Scoped to (viewer,
   session), revocable, auto-expires on share stop / session end / TTL (1h).
   Never persisted across sessions.
4. **The only primitive is "propose a prompt":** structured `{viewer, session,
   text}`, length-capped, control-chars/escapes stripped. No raw-keystroke path
   exists in the codebase at all — absent, not disabled.
5. **Owner preview-before-inject by default.** The proposal lands in the owner's
   approvals UI with Approve / Edit-then-approve / Reject. Nothing reaches the
   agent until the owner acts.
6. **Safe injection:** only when the agent is idle at its prompt (never mid-turn,
   never while a permission prompt is showing — else the text could be eaten as
   a "1" that approves a tool). Inject via `tmux load-buffer` + bracketed
   `paste-buffer` (literal), then one Enter, prefixed
   `[Prompt from remote collaborator Bala via RubberTerm]:` so agent and
   transcript know it wasn't the owner.
7. **Permission-mode cap while shared:** refuse write-sharing on any session
   running `--dangerously-skip-permissions`/`bypassPermissions` or `acceptEdits`;
   while a write grant is live, set `permissions.disableBypassPermissionsMode:
   "disable"` so it can't be re-entered. The agent's own tool-permission prompts
   still fire — and render **only for the owner**. (Note: even default mode runs
   a built-in read-only command set + the repo's accumulated
   `settings.local.json` allowlist promptless — so the *prompt itself* is the
   attack surface. Preview-before-inject is what makes that acceptable.)
8. **Audit log** (append-only, on the owner's Mac): viewer, timestamp, exact
   text, decision, injection time; attribution visible inline in the transcript.
9. **Kill switch:** one action revokes all grants, drops viewers, clears the
   proposal queue; grants also die on session end / server restart.
10. **Rate limit:** one pending proposal per viewer; cap injections (e.g.
    10/10min/viewer).

Later (opt-in, per trusted human, still idle-only/mode-capped/audited):
auto-inject; force `plan` mode while shared with a one-click "run B's plan";
delegated approvals (B may answer specific tool prompts — Warp does this, we
don't in v1); full raw-keystroke control (distinct consent ceremony + persistent
banner; likely never needed to "redirect the agent").

### v3 — hand it off: sessions that outlive the laptop (build when handoff demand is real)

The same server image runs on a $5–6/mo Fly machine/VPS and dials the SAME relay
as just another device — because the relay treats hosts identically, a cloud
host is indistinguishable from the laptop, and v1/v2 work is 100% reused. A
"handoff" moves the session's repo state (branch push + conversation
export/resume) laptop→cloud. No E2B/Modal sandboxes (idle-billed, 24h caps,
~$70+/mo vs ~$6 for the wrong shape). This is where accounts/teams/pricing enter
(the market monetizes exactly here — Conductor $50/mo, Devin Teams, Claude Code
Team). Not designed further now.

### Explicitly rejected

- **WebRTC / true P2P** — cannot remove the central server (signaling needs one;
  TURN carries all traffic for 30–70% of corporate-network viewers), costs the
  same as a relay at KB/s, and drags heavy native deps (aiortc→PyAV) that break
  the zero-dependency install. sshx, tmate, and Live Share's remote path all use
  relays. The "compute stays on my laptop" value P2P promises, the relay already
  delivers.
- **Per-user tunnels (Cloudflare named / ngrok / frp) as the mechanism** —
  hostname routing forces a per-user domain/account (named tunnel), or metered
  cost that grows with usage (~$100/mo at 100 users, ngrok), or broken tenant
  isolation (frp's global token). All also expose the whole local server. The
  relay's path-based routing under one domain is strictly better.
- **Building on Liveblocks/Yjs/CRDT infra** — the PTY is single-writer; there is
  no merge problem anywhere. Presence is ephemeral broadcast, a counter, not a
  CRDT.
- **GitHub OAuth as the v2 write identity** — magic-link email matches the invite
  channel; GitHub is a later optional second IdP whose only value is
  auto-approving *requests* from repo collaborators, never a replacement for
  owner approval.
- **E2E frame encryption in v1** (sshx fragment-key) — the relay is first-party;
  document the concrete trigger (a third party runs a relay, or a paid
  multi-tenant tier) and add the ~200 lines then.
- **Slack as v1** — a good later notification channel; doesn't remove the relay
  + share page and adds an OAuth/app-review surface now.

## Research round 2 (what reversed the v3 decision)

Four deep research passes after the v3 "tunnel, no relay" draft failed the
"second user exists" test:

- **Relay build-vs-buy** — the decisive finding: tunnels route by hostname (⇒
  per-user domain/account, a non-starter), a relay routes by path (⇒ one domain,
  zero user setup). A relay also exposes only pushed frames vs a tunnel exposing
  the whole local server. Verified the "buy under one domain" options (CF Tunnel
  programmatic = free but supervises `cloudflared` per user + exposes whole
  server; ngrok = ~$100/mo at 100 users; frp = broken tenant isolation). Build
  the relay, ~$5/mo flat.
- **WebRTC / P2P** — rejected: can't remove the central server (signaling +
  TURN), same cost as a relay at KB/s, breaks the zero-dep install; the relay
  already delivers "compute stays on the laptop."
- **Remote-prompt safety** — Omnara types remote text straight to stdin with no
  approval and no second-person model; Live Share/Warp/tmate all gate write as a
  separate credential + host approval + host-side enforcement. Yielded the v2
  "propose-a-prompt, owner-approves, mode-capped, injected by us" design and its
  10 must-haves.
- **Identity + follower** — device auth = keychain-stored opaque bearer (ngrok
  model); viewer read = link-capability, viewer write = magic-link email (matches
  the invite channel) over GitHub OAuth; "follower" for a terminal = a
  live/reviewing state + viewer list + join notifications, no CRDT.

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

1. **Domain + relay host.** v1 needs one domain (e.g. `share.rubberterm.com`)
   and one small always-on box (Fly.io ~$3–5/mo or a Hetzner CX22). That's the
   only infra we operate. Ready to stand that up, or want it spec'd so someone
   else can?
2. **Positioning.** Sharing is the paid tier across the whole market
   (Conductor $50/mo, Devin Teams, Claude Code Team). Start the paid product
   here, or keep it free while single-user features stay the product?
3. **Demo bar.** v1's read-only watch + scoped ask + presence is live and
   multiplayer — it clears the YC "not a read-only transcript" line. Enough to
   demo, or pull v2's propose-a-prompt (write) into the first release despite
   its RCE surface and the extra auth/approval machinery it needs?
4. **Build v1 now, or hold?** The doc is decision-ready. Nothing is built yet.
   v1 = the relay + device auth + the read-only viewer/scoped-ask/presence on
   the laptop.
