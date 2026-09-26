# Control tower — design

Status: draft for review, 2026-09-25. Nothing built. Clickable prototype
built from a snapshot of 21 real sessions:
https://claude.ai/artifact/C1JjwNBz2fgyjx7Jy2DFwj (private to the owner).
Builds on [oracle-design.md](oracle-design.md).

## The ask

Clicking **Oracle (fleet monitor)** opens a page, not a side panel. The page
shows:

- **Insights:** how many agents there are, total tokens used across sessions,
  when the last backup ran, how many remote sessions exist, and other useful
  facts.
- **Ducks:** an animated scene with each agent drawn as a duck, grouped into
  teams by folder.
- **Hover:** hovering a duck shows its name and what it is working on.
- **Messaging:** you can message a session directly from the page.

## Page layout

```
┌ ← Sessions   Control tower  Oracle · fleet monitor            ● live ┐
│ [Agents] [Needs you] [Tokens · 7d] [Agent mail · 24h] [Last backup] [Remote] │
│ fact chips: busiest team · largest context · most sub-agents · looks stuck │
├──────────────────────────── teams (ducks) ─────────┬── right rail ───┤
│ Duckterm  1 busy · 6 idle                          │ Needs you (6)    │
│   🦆 🦆 🦆 🦆                                        │ Ask Oracle       │
│ Nourish   3 waiting · 1 idle      Entourage …      │ Legend           │
└────────────────────────────────────────────────────┴─────────────────┘
```

- **Entry point.** The topbar's Ask Oracle button becomes **Oracle**. It
  opens the control tower as a full view in place of the three panes, the way
  the folder grid view takes over the window. "← Sessions" returns to the
  panes. The Ask Oracle chat moves into the page's right rail. The docked
  chat panel stays reachable from inside the page, so there is still one
  chat.
- **Hidden terminals.** Opening the tower must not resize hidden terminals.
  The 2026-09-25 bug B5, "Oracle open/close drops the PTY resize", came from
  exactly this kind of layout switch. Terminals stay mounted but unmeasured
  while the tower is shown.
- **Narrow windows.** Below 1000 px the right rail moves under the teams, and
  the tiles wrap to three per row, then two.

## Insight tiles

Every tile states its scope, and a tile with nothing true to show says so
rather than showing zero.

| Tile | Definition | Source | Cost |
| --- | --- | --- | --- |
| **Agents** | Sessions that aren't archived, split into busy, waiting, and idle, plus the number of teams | Session rows the dashboard already streams | None |
| **Needs you** | Sessions in `waiting`, and how long the oldest has waited. Amber when non-zero | Session rows | None |
| **Tokens · 7 days** | Tokens processed by Claude Code and Codex, the share read from cache, and output written. Split by agent | Claude transcripts: summed per-reply `usage`. Codex: the last `total_token_usage` in each rollout file | First scan of 476 MB took 1.5 s. Cache per file by (path, size), rescan only the growth, refresh every 5 min in a worker thread |
| **Agent mail · 24h** | Agent-to-agent questions sent and answered, and Oracle nudges | `session_questions`, `OracleNudge` events | One query |
| **Last backup** | Time of the last completed backup and its destination type. Amber when none or older than 7 days | `backup-state.json` job, plus GCS sync receipts when present | File read |
| **Remote sessions** | Sessions running on the remote workspace | Not on main yet. Shows "Remote workspace isn't set up yet" until branch `remote-session` merges | None |

On tokens, the real numbers show why the split matters. For the 7 days to
2026-09-25, Claude Code processed about 10.2B tokens and Codex about 0.74B.
About 97% of that input was read from cache, and only 29.6M tokens were
written. A single "tokens used" figure would overstate the real work by a
wide margin, so the tile leads with the total and states the cache share
beside it.

**Scope decision needed:** count only transcripts belonging to DuckTerm
sessions (the session row's transcript), or every Claude Code and Codex
transcript on the Mac (what the prototype shows)? This doc recommends DuckTerm
sessions for the tile, with the machine-wide figure in the tile's tooltip.

## Facts

A row of short, specific facts, each shown only when true:

- Busiest team (most agents).
- Largest context, flagged when above 80% of the model's window.
- Most sub-agents running.
- **Looks stuck:** `busy` with no event for 30 minutes and an unchanged
  screen. This is the stale-state rule listed in the Oracle design. In the
  snapshot, Entourage's ui-dev showed busy with no activity for 3 days.
- Longest wait on you, when over a day.
- A failed or missing backup.

## The duck scene

- **Teams are top-level sidebar folders**, largest first. Nested folders sit
  inside their top-level team, and the hover card names the subfolder.
  Ungrouped sessions form a "No folder" team.
- **Each duck is the existing mascot** from `Duck.tsx`, the approved v4 design
  with no water. Its poses already encode state:

  | State | Pose | Extra |
  | --- | --- | --- |
  | busy | At the laptop, typing | None |
  | waiting | Checking a wristwatch | Amber pulse ring |
  | idle | Floating calmly | Drifts slowly around its spot |
  | stopped, interrupted | Asleep, with z's | Faded, at the team's edge |
  | looks stuck | Idle pose, faded | "?" badge |

- **Badges:** a blue envelope with the unread-mail count.
- **Ducklings:** a trail of small ducks for sub-agents, up to three, then
  "+N". The session row already carries the sub-agent list.
- **Placement is deterministic:** a loose grid per team, with jitter seeded by
  the session key. The scene looks the same on every load, and ducks never
  overlap. Each row is 88 px tall, so a team's height follows its row count.
- **Motion:** only the existing pose loops plus a slow drift for idle ducks.
  All of it stops under `prefers-reduced-motion`. It's CSS transforms only, so
  60 ducks stay cheap.
- **Beyond about 60 agents,** ducks shrink and names show on hover only.

## Hover and pin

- **Hovering or focusing a duck** shows a card:
  - Name and state pill.
  - Folder, agent type, model, and when it last updated.
  - **Working on:** the first sentence of the session's progress digest.
  - The tool it's running, when busy.
  - Its context size.
  - Unread mail and sub-agent counts.
- **Clicking pins the card** and opens a message composer. Escape or a click
  outside unpins it.
- **Every duck is a button** with an `aria-label` of its name, team, and
  state, so the scene works from the keyboard.

## Messaging a session

The composer offers two delivery modes, because typing into an agent's
terminal is only safe at certain moments.

| Mode | What happens | When it's offered |
| --- | --- | --- |
| **Inbox message** (default) | An owner message to that one session. It uses the broadcast record shape with a single recipient, through a new owner-token `POST /sessions/:key/message`. The agent sees it at its next turn end (Stop-hook notice), or Oracle wakes it within a minute or two if it's idle | Always |
| **Type into its prompt** | Pastes the text as a new prompt, like typing it | Only when the session is idle, its prompt is empty on screen, and nobody typed there in the last 2 minutes. These are Oracle's nudge gates, checked again on the server at send time, which refuses with the reason |

Typing is never offered for `waiting` sessions. A waiting agent may be
showing a permission menu, where pasted text would select an option. For
those, the card offers **Open terminal** instead.

Limits: 16 KiB per message, the existing broadcast size. Rate limits reuse
the owner broadcast path.

## Data and API

- The ducks and the Needs you list use the session state the dashboard
  already receives over its event stream. No new polling is needed for them.
- A new `GET /control-tower` endpoint returns the tiles and facts in one
  payload. It's loopback-gated like other GETs, and the token scan behind it
  is cached as described in the tiles table. The page refreshes it every 60 s
  while open.
- Inbox messages need the new `POST /sessions/:key/message` endpoint. Typed
  prompts reuse Oracle's paste path.

## Phases

1. **Read-only tower:** the page, the tiles, the facts, the duck scene with
   hover, the Needs you list, and the chat in the rail.
2. **Messaging:** the composer with both modes and the new endpoint.
3. **Polish, driven by use:** more facts, an "Oracle activity" strip listing
   recent nudges and why sessions were skipped (the status view from the
   WhatsApp design), and clicking a team to open its folder grid.

## Open questions for the owner

1. **Token scope:** DuckTerm sessions only, or everything on this Mac?
2. **Entry point:** should the Oracle button open the tower, replacing the
   docked chat as the default?
3. **Resting ducks:** show stopped and interrupted sessions in the scene
   (asleep, faded), or leave them out?
4. **Anything missing from the tiles?** Candidates: commits today across
   worktrees, releases this week, and approvals pending.
