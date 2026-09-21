# AGENTS.md as an evolving, typed template

## Problem

AGENTS.md today is a free-form textarea per folder. Nothing keeps it alive:
rules are untyped prose, there is no way to scope a rule to one agent runtime,
no provenance (why does this rule exist?), no dedupe target for the suggest
loop, and no forcing function to review it. It starts empty and stays empty.

## Design

One file per folder stays the source of truth — plain markdown that any agent
reads directly, with duckterm layered on top. Structure comes from **typed
blocks**: each rule is a markdown list item (or short paragraph) preceded by an
HTML-comment header carrying machine-readable metadata. Agents reading the raw
file see the metadata inline (comments are visible in raw text, invisible when
rendered); duckterm's tooling parses it.

### Block wire format

```markdown
<!-- rule {"id":"no-piped-gates","scope":"all","status":"active","source":"retro","evidence":3,"added":"2026-09-21"} -->
- Never pipe the gate or tests through grep/tail/head — pipelines report the
  filter's exit code. Run `scripts/gate.sh` bare; the log is the verdict.
```

A block is the comment plus the content lines that follow, ending at the next
blank line, `<!-- rule` comment, or heading. Headings (`##`) group blocks for
human reading and carry no metadata.

### Block type

```python
@dataclass
class RuleBlock:
    id: str          # kebab-case, unique per file; the dedupe key
    scope: str       # "all" | runtime ("claude-code", "codex")
                     #       | runtime/model ("claude-code/fable-5")
    status: str      # "active" | "candidate"
    source: str      # "manual" | "correction" | "digest" | "retro"
    evidence: int    # how many observed signals back this rule
    added: str       # ISO date
    text: str        # the markdown content lines
```

Every field has a consumer; anything without one was cut:

- `id` — dedupe: the suggest loop must not re-propose a rule that exists.
- `scope` — the point of the exercise: rules that apply to one runtime only.
- `status` — machine-proposed rules land as `candidate` and never silently
  become `active`; a human promotes them by saving.
- `source` + `evidence` — the reviewer's trust signal ("3 corrections said
  this" reads differently from "someone typed it once"), and the retirement
  signal (a candidate whose evidence stops growing gets dropped at review).
- `added` — staleness at review time.

Deliberately absent: `category` (markdown headings already group blocks),
`last_confirmed` / `expires` (no consumer until a retirement pass exists),
per-block authorship (git blame answers that).

### Scoping rules for agents

Scoped blocks stay in the shared file — a codex agent reading raw AGENTS.md
sees claude-scoped blocks with their scope label and skips them; models follow
"applies to X only" reliably. Two delivery tiers:

1. **Without duckterm** (someone opens the folder in a bare CLI): the raw file
   is self-describing. Scope labels are in the block headers.
2. **With duckterm**: `helpers/session_instructions.launch_prompt()` already
   prepends per-runtime instructions at session launch. Extend it to append
   the blocks whose scope matches the launching runtime — the agent gets its
   scoped rules explicitly, not by convention.

Scope granularity today is **runtime** (`sessions.runtime` is recorded).
`runtime/model` syntax is reserved but unusable until events record the model
— that is the prerequisite, not speculative future-proofing: fable-5 vs
smaller models already warrant different guidance (context budget, tendency
to over-engineer).

## The evolution loop (recurring practice)

Three sources feed candidates; a human always saves. Nothing writes the file
autonomously — same contract as today's suggest button.

1. **Suggest from corrections** (exists; upgrade). Emits typed `candidate`
   blocks instead of bare lines. Scope is inferred from evidence: if every
   correction came from codex sessions, the block is scoped `codex`;
   mixed → `all`. Evidence = correction count. Dedupe against existing ids.
2. **Digest bridge** (new). The progress digest already tracks
   `user_learnings` with recurrence evidence across sessions. A learning
   whose recurrence crosses the same threshold that earns it an evidence bar
   (≥3) is exactly an AGENTS.md rule candidate — file it as one, scoped to
   the runtimes it was observed in.
3. **Manual** — the editor, as today. Saved rules get `source:"manual"`.

**The forcing function**: the AGENTS.md button shows a badge with the pending
candidate count for the selected folder (same pattern as inbox badges). A
badge you see on every glance at the dashboard is the recurring practice —
no cron, no scheduled review, no nag modal.

**Retirement**: at review (editor open), candidates older than 30 days whose
evidence hasn't grown are listed for deletion. Active rules are only ever
retired by a human deleting them.

## Implementation plan

| Step | What | Where | Notes |
|------|------|-------|-------|
| 1 | `RuleBlock` parse/render/merge | `core/agents_rules.py` (new) | Pure functions + unit tests. Ships in the same change as step 2 — a parser with no consumer is dead code. |
| 2 | Suggest loop emits typed scoped blocks | `llm/suggest.py`, `server.py` `_suggest_agents_md` | server.py is contested — index-level edit protocol. |
| 3 | Launch-prompt injection of scoped blocks | `helpers/session_instructions.py` | Follows the existing collaboration-guide pattern. |
| 4 | Candidate badge + candidate styling in editor | `web/src/AgentsMdModal.tsx`, `App.tsx` | App.tsx contested. |
| 5 | Digest bridge | `server.py` `_refresh_progress` | Only after 1–4 prove out. |
| 6 | Record model per session | hook payload → sessions table | Unlocks `runtime/model` scope. |

What breaks without each piece: without 1–2 the template stays untyped prose
(the current state); without 3 scoped rules rely on agents honoring labels;
without 4 there is no recurring practice, only a button nobody remembers;
without 5 the richest correction signal (digest recurrence) is wasted;
without 6 "model-specific" means "runtime-specific". Steps 5–6 wait until
the loop demonstrably produces rules worth keeping.
