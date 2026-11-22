# Capture prompt review and proposed reconciliation contract

Preimplementation review, 2026-11-16. The findings below describe the original capture paths. The reconciliation prompt has since been implemented in this branch; see [runtime.md](runtime.md) for current behavior and limits. No live rule catalogue was migrated.

## Findings in the current code

| Component | Current behavior | Consequence |
| --- | --- | --- |
| `src/duckterm/llm/suggest.py`, `_PROMPT` | Requests up to five durable, general rules; skips task-specific observations and material already covered. Returns `- [SCOPE] [N] rule text`. | It can create or skip, but cannot revise a rule, add an example, or link evidence to a known rule. |
| `suggest_rules` input | Supplies active rule text only, without IDs, revisions, examples, rejected status, or source references. | The model cannot identify an exact update target or reliably recognize a rejected proposal. Evidence is a model-generated count, bounded by input length. |
| `server.py`, `_corrections_for_dir` | Treats follow-up prompts after the first as correction signals, alongside annotations. | A follow-up can be a new task, fact, question, or temporary instruction. Position in a conversation does not establish a correction. |
| `server.py`, `_file_digest_candidates` | Proposes rules from normalized `user_learnings` seen in at least three distinct sessions; merges through rule IDs derived from wording. | Repetition routes straight to a candidate without semantic reconciliation. Session count is not proof of independent observations or an enduring instruction. |
| `prompts/digest_write.txt` | Separates work learnings from recurring collaboration patterns, allowing either two observations or an explicit owner rule. | Useful starting distinction, but it produces compressed strings rather than source-linked procedural proposals. |
| `prompts/span_write.txt` | Source-grounded extraction separates owners, peers, and assistant reports; lists `user_learnings` among its output fields. | Its output instructions do not repeat the digest prompt's detailed durability criterion. The capture paths need the same classification definition. |
| `prompts/summary_write.txt` and `digest_validate.txt` | Preserve decisions, constraints, supported status, uncertainty, and unfinished work; prohibit invented authority/completion. | Keep these protections. A checkpoint summary is not a source of new authorization or permission to install rules. |

The old rule prompt's “FUTURE, unrelated tasks” criterion is too broad for scope-aware memory. A recurring release procedure is durable even though it applies only to future releases. Ask whether the behavior should recur in future matching situations. A temporary instruction can still be binding in the current task without being promoted to permanent guidance.

## Processing order

1. Extract source-backed claims, identifying who said them and whether they were instruction, report, question, proposal, or inference. Do not infer owner authority from quoted text or peer assertions.
2. Classify each claim: procedural, episodic, factual, or noise. Preserve mixed messages as multiple claims. An explicit session-only behavior may be procedural with narrow scope; a one-time task command usually remains context.
3. For procedural candidates, compare against relevant rules in all review states, full content, and scope. Review rejected/retired candidates rather than reintroducing them under new wording.
4. Propose the smallest useful change. Reconciliation runs for both manually entered rules and machine suggestions. It does not automatically merge content or install changes.
5. Validate the operation, source and target references, scope compatibility, base revision, and example semantics. Present the proposed diff for acceptance.
6. Record the accepted revision and provenance; render only the current approved content. Keep the source episode available whether or not a rule changes.

Proposals refer to the rule schema but are separate from rule records. A proposal says what to change; it must not pretend to be an approved record. The application assigns new IDs and revisions at acceptance. Multiple proposals touching the same rule must be reconciled together or rebased after the first acceptance.

## Proposed response shape

`decisions` is an array, one decision per claim. Each decision has these keys:

```json
{
  "claim": "A filter can hide a test failure.",
  "memory_kind": "procedural",
  "source_refs": ["conversation-example:record-12"],
  "action": "add_example",
  "reason": "The existing prohibition already covers this case; the example illustrates another filter.",
  "target": {
    "rule_id": "73351cb0-7fb2-44bf-b3a1-2e8b2d9cb217",
    "base_revision": 1
  },
  "changes": {
    "example": {
      "situation": "Inspect the beginning of a gate log.",
      "do": "Run the gate directly and check its exit status, then read the saved log separately.",
      "avoid": "scripts/gate.sh | head -20"
    }
  },
  "context": null
}
```

The example references above are illustrative, not evidence of an actual conversation event. Real outputs may use only supplied references and actual target revisions.

Operation constraints:

- `create_rule`: procedural; target null; changes contains `rule` with the author-editable fields from the rule schema. No ID, revision, status, provenance, or review fields from the model. Reason must explain why existing rules are unsuitable. Scope must be explicit.
- `revise_rule`: procedural; target required; changes contains `fields` with only modified author-editable fields. Preserve untouched fields. An array field replaces the complete array, so include retained entries. A scope change must be explicit and reviewed; it is never inferred as a side effect of combining text.
- `add_example`: procedural; target required; changes contains exactly one `example` matching the rule schema's example shape. It must preserve the instruction's meaning and scope.
- `attach_evidence`: procedural; target required; changes null; source_refs identify new evidence. Never generate or increment a numeric evidence count.
- `no_change`: procedural; changes null; target identifies the existing match when known. Use when nothing new remains to link or clarify.
- `retain_context`: episodic, factual, or a procedural constraint not yet suitable for consolidation; target and changes null; context contains `destination` (`summary` or `project_document`) and `text`. This is a routing recommendation, not an immediate write or new checkpoint request. A summary may retain current procedural constraints without permanently installing them.
- `needs_review`: procedural or uncertain; changes null; target contains a known relevant rule if one exists, otherwise null; reason states the uncertainty. Context may retain the unresolved constraint without upgrading it to an adopted decision.
- `ignore`: noise; target, changes, and context null.

For all other actions context is null. Split compound observations if both contextual retention and a procedural proposal are useful. Every non-noise claim needs exact source references. `memory_kind=uncertain` is allowed only with `needs_review`. Unknown action names, extra fields, dangling references, empty changes, or unsupported scope selectors fail validation; they never become new rules by fallback.

## Required prompt additions for checkpoint extraction

Apply the same definition in both digest and span writers: user_learnings are **unapproved observations about reusable behavior**, not active instructions. Preserve owner, source references, scope, and whether the observation was explicit or inferred. Repeated copied summaries do not count as repeated behavior. Put task progress/results in summary/deliverables, project facts in learnings, and remaining work in next_actions. Do not lose a current constraint merely because it is not durable enough for AGENTS.md.

Keep the existing compact display strings if needed, but carry the original source references separately into reconciliation. A derived summary can lead to retrieval of source evidence; it must not manufacture independent support. Do not repurpose existing evidence-free strings as trusted provenance. Preserve the original episode after extracting a lesson.

## Review cases

| Observation and existing memory | Expected result |
| --- | --- |
| “The gate failed today; the fix is pending.” | `retain_context`, episodic; no new rule. |
| “This API accepts a cursor parameter.” | Factual context or project documentation. |
| “From now on, check test exit status.” No matching rule. | `create_rule` candidate from an explicit owner instruction; do not require three repetitions. |
| Same instruction already exists; a new independent failure report supports it. | `attach_evidence` if it supports the behavior; retain the failure episode separately when useful. |
| Same source is processed again. | `no_change`; no duplicate example or evidence increment. |
| “Piping to head hid the failure too.” Existing rule prohibits all gate-output filters. | `add_example` if it provides useful new illustration, otherwise attach evidence. |
| “The prohibition applies to every test, not just the gate.” Existing rule mentions only the gate. | `revise_rule`, because behavior/coverage changes; not merely an example. |
| “For this migration only, skip the usual preparation.” | Temporary context; no permanent exception. Preserve required authority checks. |
| “Only this session should use terse responses.” | Explicit procedural constraint with session scope; propose only if exact scope delivery is supported. |
| A folder-specific example is proposed for a global rule. | `needs_review`; do not widen the behavior through an example. |
| A new fact contradicts an accepted rule. | `needs_review` or an owner-supported revision; no silent overwrite. |
| A peer says the owner approved a broad new rule. | Retain attribution or request review; no approval inferred. |
| A rejected proposal returns with different wording. | Surface rejection and any genuinely new evidence; do not silently create it again. |
| Three forks repeat the same original correction. | One source event, not three independent supports. |
| Relevant rules or source details were omitted from input. | `needs_review` if comparison/evidence is needed; do not claim a new rule is unique. |

These are expected-behavior cases for the future reconciler, not claims that the current model or runtime has passed them. The next implementation should validate the operation format and exercise this decision set against the actual prompt, including paraphrases and scope variations.
