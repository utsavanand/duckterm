# Plan hand-off — plan with one agent, implement with another

Status: designed 2026-09-25 at the owner's request (via the `product`
session), not implemented. Needs a UI preview before build.

## What it is

Plan with a model that is good at planning (say Claude Opus), then implement
with a different agent or model (Codex, or a cheaper Claude), without
retyping the plan or losing the connection between the two.

## The decisions, and why

**1. Same worktree, not a fork.** The planner and implementer are two halves
of one task; the point of the hand-off is that the implementer works on the
code the plan was written against. A fork creates a second worktree and an
immediate divergence — useful for *parallel attempts*, which is what the
existing fork already does, and the wrong shape here. The user can still
fork afterwards if they want attempts.

Consequence to state plainly in the UI: two sessions now share a working
tree. That is the same situation as two agents in one directory today, and
it carries the same hazard — concurrent edits. v1 mitigates by convention
(the planner is expected to stop), not by locking. Do not claim isolation
that is not there.

**2. The plan comes from the planner's last assistant message, editable.**
Not from Claude's plan-mode file: that is Claude-specific, so keying the
feature to it makes hand-off unavailable for Codex planners and couples us
to another tool's on-disk format. The last assistant message is universal
across harnesses and is already available — `read_transcript` and the
Messages view both use it.

The user edits the seed prompt before sending. This is not optional polish:
a plan written *for a human reader* usually needs a sentence of framing to
work as an instruction ("implement this plan; do not redesign it"). Default
the box to the last assistant message, let the user pick an earlier turn,
and let them type freely.

**3. Lineage, reusing what exists.** The implementer appears as a child of
the planner in the session tree. The sub-agent tree already renders
parent/child lineage, and forks already record a parent, so this is
consistent with both rather than a new concept.

## Flow

1. **Hand off** action on a session (detail panel and session row).
2. Modal: pick agent (claude-code / codex / copilot) and, where the harness
   exposes it, model; see the seed prompt prefilled with the planner's last
   assistant message; edit it; optionally pick an earlier turn.
3. Launch a new supervised session in the **same cwd/worktree**, with the
   edited text as its initial prompt, `parent = planner`.
4. The planner is left alone — never auto-stopped. The modal says the
   planner is still running and offers a Stop checkbox, defaulted **off**.

## What this is not

- Not a fork (no new worktree, no branch).
- Not a conversation transfer. The implementer starts fresh with the plan as
  text. Transcripts are harness-specific — a Claude conversation cannot be
  continued by Codex — and this design is honest about that rather than
  implying continuity. Same constraint as F4 (migrate a session to another
  harness) in the roadmap: seeded, not resumed.
- Not automatic. Nothing is handed off without the user reading the prompt.

## Relationship to existing routes

`POST /sessions/:key/fork` (git fork) and `/fork-conversation` (Claude-only
conversation fork) both stay as they are. Hand-off is a third, simpler
thing: launch-in-place with a seeded prompt and a recorded parent. It should
reuse the existing launch path rather than growing a fourth spawn mechanism
(RETRO: bulk operations that reimplement a subset of the single-item path).

## Meta-harnesses

A hand-off crosses harnesses, so the implementer's project may have a suite
installed that the planner's did not, or vice versa. v1 does nothing clever:
the new session inherits whatever the directory already has. The
compatibility question belongs to F5 (meta-harness vocabulary and
composition) and should not be solved here.

## Tests

- Hand-off launches in the planner's cwd, with the edited text as the
  initial prompt, and records the planner as parent.
- The planner keeps running unless Stop was explicitly checked.
- Seed defaults to the last assistant message; picking an earlier turn seeds
  that one instead.
- A harness with no model selector still hands off (agent choice only).
- Lineage renders planner → implementer in the tree.

## Open question for the owner

Model selection per harness is uneven — Claude Code takes `--model`, Codex
takes `-c model=…`, Copilot differs again. v1 can either expose a free-text
model field per harness (honest, ugly) or only offer agent choice and let
the user set the model in the session (simpler, one less thing to get wrong
per harness). Recommend the latter unless model choice at hand-off is the
point for the owner.
