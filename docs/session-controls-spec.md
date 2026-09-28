# Session controls: right-panel actions, Restart, Change model, Switch harness — spec

Status: proposed by product for the owner, 2026-09-27. Owner asked to "think
deeply, spec out these features, then ask the architect to design them".
Not designed or built.

## Summary

Move a session's actions into a **Session** card at the top of the right
panel, so they're visible in every sidebar density. Add three actions:
**Restart**, **Change model** and **Switch harness**. All three reuse the
existing Resume path (`POST /sessions/:key/resume`, `server.py` `_resume`),
which already relaunches an agent in its saved folder with the current CLI
binary and carries the conversation for Claude Code, Codex and Copilot.

## Problems today

- **Compact density hides actions.** Rename, Ungroup, Fork, Notes,
  Checkpoint, Stop, Archive and Delete are an inline row under each session.
  Compact hides that row (`sidebarDensity.css:20`) behind a small `⋯` button
  that expands it in place. The owner switches to Standard to find them, so
  the `⋯` is not discoverable enough.
- **The right panel is mostly empty** for a selected session: Edit file, a
  summary, harness and model, context use, branch, theme, then connectors.
- **No way to restart a live session** to pick up an updated CLI or changed
  MCP connections. Today: Stop, then Resume.
- **No way to change model or harness** without starting a new session.

## 1. Session card in the right panel

Top of the right panel, above Edit file:

| Row | Contents |
| --- | --- |
| Identity | Session name (click to rename), folder, state |
| Harness | e.g. "Claude Code 2.1.283", with **Switch harness…** |
| Model | e.g. "claude-opus-5-5", with **Change model…** |
| Actions | Restart, Checkpoint, Fork, Notes, then a `More` menu: Ungroup, Move to remote, Stop, Archive, Delete |

- The actions shown follow the same rules as today: no Fork on a
  non-git session, no Resume on an archived one, and so on.
- Destructive actions (Stop, Archive, Delete) stay in `More` and keep their
  confirmations.
- The left sidebar keeps a `⋯` on each row in **every** density, opening a
  popover menu with the same actions instead of expanding the row. Standard
  and Relaxed can keep the inline buttons; the owner decides (question 1).
- Nothing new on the server for this part; it moves existing buttons.

## 2. Restart

**Goal:** relaunch the agent in place to pick up an updated CLI binary,
reloaded MCP servers and connectors, refreshed hooks and instructions,
without losing the conversation.

**Behavior:**

1. Stop the agent's process (the same stop the Stop button uses).
2. Relaunch through the existing resume path: same session card, same folder
   or worktree, same session key, conversation carried natively where the
   harness supports it.
3. Show the result: "Restarted — Claude Code 2.1.283 → 2.1.290, conversation
   continued", or "…started a fresh conversation with notes" when the
   transcript can't be resumed (resume already reports `carried_conversation`).

**Safety:**

- If the agent is **busy**, confirm first: "The current turn will be
  interrupted and its unfinished work lost." Idle or waiting: restart
  without asking.
- An unsent draft in the terminal is lost; warn when the pane has one (the
  supervisor already tracks owner keystrokes).
- Out-of-band processes the agent started (dev servers, background jobs)
  die with the terminal. The existing resume nudge already tells the agent.
- **Codex hazard** (raised by the architect earlier): confirm Codex's native
  resume carries the conversation across a CLI version change before
  claiming it does.

## 3. Change model

**Goal:** switch the selected session's model, e.g. Opus to Sonnet, or
GPT-5.3 to a faster one, keeping the conversation.

**Proposed mechanism: restart with a model flag.** Restart as in section 2,
adding the harness's model argument to the relaunch (`claude --model <id>`,
`codex -c model=<id>`; Copilot to be checked). The conversation continues
through native resume.

**Not proposed:** typing `/model <id>` into the terminal. The owner has said
agents shouldn't be driven by typing into their terminal (F11), and typing
fails silently when a menu or a draft is on screen.

**Picker:** lists the models the harness supports, with the current one
marked. Where DuckTerm can't list them reliably, a short curated list per
harness plus a free-text field. The model shown on the card already comes
from the transcript (`claude_code.py` reads the last assistant call's model),
so a successful switch is visible on the next reply.

**Persisted:** the chosen model is saved on the session, so later Resume and
Restart keep it.

## 4. Switch harness (roadmap F4)

**Goal:** move a session from one coding harness to another, e.g. Claude Code
to Codex, in place.

**The hard constraint:** conversations don't transfer between harnesses. A
Claude transcript can't be resumed by Codex. The switch starts a **fresh
conversation seeded with notes**, and the UI must say so plainly. This is
already the rule for F4 and F7 plan hand-off.

**Behavior:**

1. Pick the new harness (Claude Code, Codex, Copilot) and optionally its model.
2. Preview the notes the new agent will receive: original task, branch,
   where it left off, recent activity. This reuses `_resume_brief`, the
   notes resume already writes when a conversation can't be carried. The
   owner can edit the notes before sending.
3. Stop the old agent; launch the new harness in the same folder or worktree.
4. Keep the same session card, name and folder. Record the switch in History
   ("Switched from Claude Code to Codex"). The old transcript stays readable
   in History and Messages.

**Open question (2):** switch in place on the same card (recommended: the
card is the unit of work), or create a child session like fork does?

**Relation to plan hand-off (F7):** hand-off is "switch harness, seeded with
the planner's plan", into a new child session. Both should share the seeding
code and the notes preview.

## 5. Out of scope for v1

Switching mid-turn without confirmation; carrying a conversation between
harnesses; changing models for remote sessions until the local version
works; bulk restart of many sessions (e.g. after a CLI update), a likely
follow-up for the Agents/updates work main-dev has planned.

## 6. Open questions for the owner

1. In Standard and Relaxed, keep the inline action buttons in the sidebar,
   or move everyone to the `⋯` menu plus the right panel?
2. Switch harness: same card (recommended) or a new child session?
3. Change model: always restart with the model flag (recommended), or also
   offer the in-session `/model` command where the harness has one?
4. Restart on a busy agent: confirm and interrupt (recommended), or wait
   until the current turn ends and restart then?

## Delivery

The architect designs, the owner reviews a preview (visible UI change,
AGENTS.md), then main-dev builds the server parts (restart, model flag,
switch) and ui-dev the right panel and menus. release-dev handles QA and
release.
