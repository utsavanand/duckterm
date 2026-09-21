# AGENTS.md as an evolving, typed rule set

Status: shipped in 0.4.33. Decisions below were made with the user
(2026-09-21); the earlier comments-in-markdown draft is superseded.

## Problem

AGENTS.md was a free-form textarea per folder. Nothing kept it alive: rules
were untyped prose, there was no way to scope a rule to one agent runtime, no
provenance, no dedupe key for the suggest loop, and no forcing function to
review it. It started empty and stayed empty.

## Design

Per folder, `.duckterm-rules.json` is the **source of truth**; `AGENTS.md` is
**rendered from it** on every save (banner marks it generated — hand edits are
overwritten). One writer (`core/agents_rules.save_rules`), so the two files
never drift.

### Rule type (`core/agents_rules.RuleBlock`)

```python
@dataclass
class RuleBlock:
    id: str          # kebab-case slug of the text — the dedupe key
    text: str        # the rule
    scope: str       # "all" | runtime | "runtime/model-fragment"
                     #   e.g. "codex", "claude-code/fable-5"
    status: str      # "active" | "candidate" | "rejected"
    source: str      # "manual" | "correction" | "digest" | "retro"
    evidence: int    # observed signals backing the rule
    added: str       # ISO date
    heading: str     # markdown section it renders under
    note: str        # reviewer note (e.g. why rejected)
```

Lifecycle: machine proposals land as `candidate` and never render into
AGENTS.md; a human promotes to `active` (renders) or demotes to `rejected`.
**Rejected rules stay as tombstones** — the suggest loop and digest bridge
dedupe against every id regardless of status, so a rejected rule is never
re-proposed from the same signals. Ids are deterministic slugs of the text
(`rule_id`), so the same correction distilled twice collides on purpose.

### Scope and delivery

Sessions record `runtime` and (since 0.4.33) `model` — persisted from
transcript stats, so `claude-code/fable-5` scopes are matchable; codex doesn't
expose a model and stays runtime-scoped. Two delivery tiers:

1. The rendered AGENTS.md labels scoped rules inline (`*(codex only)*`) —
   works for any agent reading the raw file, no duckterm involved.
2. At launch, `session_instructions.launch_prompt` appends the active rules
   whose scope names the launching runtime — the agent is handed its own
   rules explicitly instead of relying on it honoring labels. "all" rules
   are NOT injected (AGENTS.md itself already reaches every agent).

## The evolution loop (recurring practice)

Three candidate sources, four review surfaces. Nothing ever writes an ACTIVE
rule autonomously.

- **Suggest from corrections** (editor button): annotations + follow-up
  prompts from the folder's sessions, each labeled with its runtime, are
  distilled by the summarizer LLM into `- [SCOPE] [N] rule` lines → typed
  candidates (source `correction`, evidence N). Hallucinated scopes fall back
  to what the evidence supports.
- **Digest bridge** (automatic): a `user_learnings` digest item recurring in
  ≥3 distinct sessions of one folder is filed as a candidate (source
  `digest`, evidence = session count, scope inferred from the sessions'
  runtimes/models). Opt-in gate: only for folders where rules.json already
  exists — the bridge must not start creating files in every watched folder.
- **Manual**: the editor's Add field (active immediately, source `manual`).

Review surfaces: the AGENTS.md button badges the pending-candidate count for
the selected folder; the launch modal shows a nudge when the picked folder
has candidates (the new agent won't see them until accepted); the editor
lists candidates first with Accept/Reject; rejected rules sit collapsed with
Restore.

## Endpoints

- `GET /agents-md?dir=` → `{rules, text, exists, managed}` — `managed` is
  false for a hand-written AGENTS.md that predates the format; the editor
  offers a line-by-line import instead of clobbering it.
- `POST /agents-md` `{dir, rules}` → saves rules.json + renders AGENTS.md.
  Legacy `{dir, text}` still writes a plain file.
- `POST /agents-md/suggest` `{dir}` → typed candidates, tombstone-deduped.

Both confined to the home tree + tmp, same as before.

## Deliberately not built

- Retirement automation (candidates stale >30 days): wait until real
  candidate volume shows it's needed; Reject is one click.
- Per-rule authorship: git blame on rules.json answers it.
- Cross-folder rule inheritance: no observed need; folders are independent.
