# DuckTerm rule protocol v2 — design proposal

Status: broader design proposal, 2026-11-16. For implemented behavior and the compatible storage format, see [runtime.md](runtime.md). This document proposes a data contract and processing rules. It does not migrate live rules or implement new scope delivery. The adjacent JSON Schema and example are reviewable artifacts.

## Recommendation

Make a rule a structured, versioned record. Keep the behavioral instruction in clear natural language, with separate fields for its explanation, examples, applicability, scope, and provenance. DuckTerm validates and resolves the records, then generates the instructions each harness can load.

A rule has two contracts: **what it asks the agent to do**, and **how DuckTerm stores, reviews, selects, and delivers it**. The second can be deterministic. JSON structure does not make arbitrary prose enforceable. A behavior that needs guaranteed enforcement needs a separately implemented check, hook, or permission boundary.

Extend the existing JSON rule store and rendering pipeline. Use the existing review UI and event machinery where suitable. No new policy language, scheduler, or server dependency is needed for this design.

## Capture and consolidation before adding rules

Owner clarification, 2026-11-16: AGENTS.md is procedural memory about how agents should behave; checkpointed context is primarily episodic memory about what happened. Treat this as a product model, not a claim that natural-language rules are executable procedures. In DuckTerm terminology, events are the evidence, summaries contain the account, and checkpoints bookmark summary revisions.

Separate three useful kinds of claim: procedural guidance (how to behave), episodic context (what happened, decisions, progress, pending work), and factual project knowledge (what is true about the system). Factual knowledge belongs in project documentation or relevant context, not automatically in AGENTS.md. Routine chatter need not become durable memory. A single owner message can contain several kinds: split its claims and link them to the same source rather than forcing the entire message into one bucket. Episodic summaries may retain procedural constraints needed to continue work; this does not itself install a permanent rule.

Before proposing any rule change, compare the new claim with existing instructions, explanations, examples, exceptions, scope, and review state. Choose the smallest supported operation:

| Decision | Use when | Result |
| --- | --- | --- |
| `add_example` | The instruction already covers the behavior, but the observation illustrates a new application without changing its meaning. | Proposed example on the same rule, preserving the instruction and scope. |
| `revise_rule` | The same behavioral principle needs clarification, correction, an exception, or a genuine extension. | A proposed revision of the existing rule, with a field-level diff. |
| `attach_evidence` | The rule and examples already cover the case; there is new, independently supported evidence. | Link the source without adding prompt text or changing the instruction. |
| `create_rule` | A durable obligation is distinct from the existing rules and has no suitable home. | A new candidate, with an explanation of why consolidation was unsuitable. |
| `no_change` | The claim or source has already been handled. | No duplicate rule, example, or evidence count. |
| `retain_context` | It records an event, result, project fact, temporary direction, or pending task. | Retain useful context in the existing summary/document workflow, without changing rules. |
| `needs_review` | Scope differs, instructions conflict, attribution is unclear, a relevant rule was rejected, or the comparison set is incomplete. | Surface the ambiguity and source evidence; do not silently merge or broaden scope. |
| `ignore` | It is routine chatter or has no durable/contextual value. | No memory addition. |

One occurrence can justify a proposal when the owner explicitly states a durable instruction. Repetition alone does not prove durability, permission, or general applicability. Copied summaries, handoffs, and forks of one event are not independent evidence. Observing a correction in one harness is evidence of where it occurred, not proof that the behavior should only apply to that harness.

An example must not smuggle in an obligation or exception. If an accepted instruction says “do not pipe the gate” and a new correction says “this applies to all test commands,” that changes coverage and belongs in `revise_rule`. If it says “piping through head hid the failure too,” the same prohibition may need only an example or evidence link. If it says “the gate failed at 14:10,” retain that episode as context.

Examples on a rule inherit its scope. Do not put a folder-specific behavior into the examples of a global rule and thereby spread it to every folder. Present a scope review or a separate narrow rule when appropriate. Treat a scope change as a separate explicit choice. Avoid giant merged rules covering unrelated obligations merely to reduce rule count.

All entry points use this reconciliation contract: manual Add, corrections, imported notes, and digest-derived suggestions. Manual Add can offer “Update existing rule” or “Add example” when a match is found; the owner chooses. Generated suggestions must show the target rule, base revision, reason, source, and before/after diff. Human acceptance is required for behavioral content and examples. Evidence-only links can be deduplicated mechanically when source identity and access are verified. Preserve the previous approved rule until a proposed change is accepted; stale base revisions require comparison again.

Use the current store and ordinary scanning over the modest rule collection first. There is no demonstrated need for embeddings, a vector database, a new scheduler, or automatic semantic merging. If retrieval later becomes necessary, record which rules were actually compared and do not claim uniqueness when relevant rules were omitted.

The reviewed current prompts and a proposed replacement are documented in [capture-review.md](capture-review.md) and [capture-prompt.txt](capture-prompt.txt). The replacement is a design artifact, not installed in the runtime. It returns change proposals, so wiring it requires an operation-aware parser, review surface, and both capture paths to adopt the same contract.

## Research and compatibility

| Source | Established behavior | Design implication |
| --- | --- | --- |
| [Codex AGENTS.md](https://learn.chatgpt.com/docs/agent-configuration/agents-md) | Loads instruction text through a global and directory-based chain. Files closer to the working directory override earlier guidance. | Preserve readable Markdown exports; do not assume arbitrary JSON metadata is interpreted by the harness. |
| [Claude Code memory and rules](https://code.claude.com/docs/en/memory) | Supports instruction files and path-scoped rules. Its documentation distinguishes instructions from enforced configuration. AGENTS.md loading depends on version and settings. | Select a supported delivery mechanism per installed harness. Keep guidance and enforcement distinct. |
| [Cursor rules](https://cursor.com/docs/rules) | Separates Markdown content from metadata such as description, file globs, and automatic inclusion. | Separate instruction content from selection metadata; avoid one overloaded scope string. |
| [JSON Schema 2020-12](https://json-schema.org/draft/2020-12) and [validation vocabulary](https://json-schema.org/draft/2020-12/draft-bhutton-json-schema-validation-01) | Defines structural constraints for JSON documents. | Publish a schema for editors, imports, and validation. Structural validity is not proof of authorization or semantic correctness. |

These sources establish useful patterns, not a shared cross-harness rule protocol. The contract below is DuckTerm's proposal.

## What the owner edits

| Field | Purpose | Requirement |
| --- | --- | --- |
| `title` | A short, recognizable name for the rule. | Required; up to 120 characters. |
| `instruction` | One actionable obligation, retaining all necessary qualifications. | Required. This is the authoritative behavioral text. |
| `explanation` | Why it exists and which failure it prevents. | Recommended; do not invent missing history during import. |
| `when` | A plain-language situation in which the instruction matters. | Optional. Included in rendered instructions; not an executable predicate. |
| `examples` | Concrete situations with the expected behavior, optionally an incorrect behavior and explanation. | Optional. Suggestions should supply one when it clarifies an ambiguous rule. |
| `exceptions` | Explicit situations where the instruction does not apply. | Optional, empty by default; reviewed with the instruction. |
| `category` | A navigation heading such as Testing hygiene. | Optional; no effect on applicability. |
| `scope` | Where the rule applies and whom it targets. | Required; always explicit. |

Explanation and examples should add information, not paraphrase the instruction. A simple rule need not require several redundant paragraphs. Examples are inert text: never run commands merely because they appear in `do` or `avoid`.

The article view shows the title and instruction first, then scope badges. Explanation and examples can expand beneath it. The edit form labels these fields “Title,” “Instruction,” “Why,” “When,” “Examples,” and “Exceptions.” Review shows the exact scope and the affected sessions before acceptance. This describes a future UI iteration; the earlier HTML preview has not been changed.

## Identity and review metadata

The application supplies `schema_version`, a stable UUID `id`, a monotonic `revision`, `status`, `provenance`, and a review reference. Editing wording does not change the ID. A rejected suggestion retains its ID and evidence references; deduplication must not depend on the first six words of the instruction.

`provenance.references` points to actual events, annotations, documents, or legacy records. An evidence count is derived from distinct accessible source records; it is not a number the model invents. Similar wording can trigger a duplicate warning but must not silently merge different rules. A reference is provenance, not authority, and is not automatically fetched or included in the agent prompt.

`review` contains a `decision_id`, the reviewed `revision`, and the decision. The server resolves that ID against a trusted owner decision and binds it to the complete payload, including scope and examples. A schema-valid JSON document claiming approval is insufficient. Importing someone else's active record yields a candidate until local approval or a trusted migration establishes it.

Lifecycle: candidate → active or rejected; active → retired. Rejected and retired records remain available for history. Editing an active rule creates a proposed revision while the previous approved revision remains effective. Acceptance replaces it atomically; rejection of the proposal does not disable the approved revision. Keep current records and pending revisions distinct in the existing persistence workflow. The schema describes a single revision; a catalogue must also enforce ID/revision uniqueness and a single effective revision per rule.

Use optimistic concurrency: submit the base revision, reject stale updates, and show the intervening change. A scope change is a meaningful revision requiring review. Evidence additions can be separate events without changing the approved content. Never let an automated suggestion directly set active status.

## Scope is an intersection

`scope.location` AND `scope.audience` must match; an optional model restriction further narrows a harness target. Unknown identities never become “everyone.”

| Location | Meaning |
| --- | --- |
| `global` | All folders in the owner's rule collection, including future folders, within existing account and delivery permissions. Not all users or all machines automatically. |
| `folder` | A DuckTerm sidebar folder, referenced by its identity. Descendant inclusion is explicit. |
| `directory` | A host-qualified filesystem directory, retained for compatibility with current per-directory rules. Descendant inclusion is explicit. |

DuckTerm's terminology distinguishes a sidebar folder from a filesystem directory. They must not be silently conflated. Existing rule files are directory-scoped. Moving them to a sidebar folder can change who receives them and must be reviewed. Folder identities must survive rename and move; if existing identifiers cannot provide that guarantee, reference migration is a prerequisite, not a hidden side effect of this proposal.

| Audience | Meaning |
| --- | --- |
| `everyone` | Every session matching the location. |
| `harness` | One harness identifier, such as `codex` or `claude-code`. Optional model matching uses an explicit selector. |
| `agent` | A stable reusable agent identity, independent of a conversation or model. |
| `session` | Exactly one stable session key. Resuming retains the rule; a fork receives a new key and does not inherit it automatically. |

Named-agent scope needs a product definition and a reusable identity. A display name, model name, or existing sub-agent process ID is not an interchangeable substitute. The schema reserves this shape, but implementations must reject activation until identity and delivery support exist. The prior preview's “UI developer” is illustrative.

Session scope lasts for that conversation, including resume, and follows its key across a harness switch. Location still constrains it. Closing a terminal is not session deletion. This corrects the earlier preview's ambiguous “ends with this conversation” wording.

New model selectors use exact matching. `legacy_contains` exists only to preserve the current substring behavior during migration; the editor does not offer it for new rules. Unknown model information fails a model-specific match.

## Resolution, conflicts, and delivery

1. Validate the record structurally, then verify references, review authority, revision, and supported scope. Unsupported schema versions and unknown properties produce errors; never silently discard fields and save a degraded record.
2. Select the current approved revision, apply location and audience filters, and explain each inclusion or exclusion. Normalize directory paths and compare path components, not string prefixes. Resolve folder ancestry through the folder hierarchy.
3. Combine applicable rules. Narrower scope does not silently cancel a broader instruction. An owner-approved `replaces` reference explicitly suppresses a replaced rule only in the new rule's matching scope. Reject missing references, self-replacement, cycles, and competing replacements for the same context. Edits to the same rule normally use a new revision, not `replaces`.
4. Flag possible prose conflicts for review. The schema cannot prove two English instructions are consistent. Do not invent automatic semantic conflict resolution or give a rule authority over harness/system policy.
5. Render the title, instruction, applicability, exceptions, explanation, and useful examples deterministically. Preserve qualifiers and complete examples; do not silently truncate to fit a token budget. Keep provenance and review history in the UI rather than repeating them in every prompt. Report an oversized export before replacing a valid one.
6. Deliver through a tested mechanism supported by the installed harness. Record selected rule IDs/revisions and the rendered content hash in existing session metadata/events. “Saved,” “prepared for delivery,” and “confirmed loaded” are distinct states; none proves obedience.

The shared AGENTS.md must not be rewritten with whichever session's private selection was last active. Session and agent rules need a supported session-specific instruction channel or an isolated launch environment. Harness labels in a shared file are advisory, not access isolation. A sidebar folder does not map automatically onto a native harness's filesystem discovery chain. If exact delivery is unsupported, show that limitation and refuse to claim the scope is active.

Codex can load instructions at run startup, so saving a record must not imply that every running conversation has refreshed it. Track the effective revision per session and use supported refresh/relaunch behavior. Continue respecting native instruction precedence outside DuckTerm's generated collection.

## Validation boundary

The adjacent `rule.schema.json` uses JSON Schema 2020-12, tagged alternatives for scope, bounded strings/arrays, and closed objects. Active/rejected/retired records require an appropriately typed review reference; candidates cannot claim one. Configure format assertion when validating UUIDs.

Application checks remain necessary: trusted decisions, equality of review and content revisions, ownership, ID uniqueness, valid references, stable identities, supported harness adapters, replacement graphs, and atomic persistence. These cannot be established by JSON Schema alone. Whitespace and rendering checks should also handle Unicode and multiline input.

Keep the server zero-dependency: implement explicit validation for this fixed contract with Python stdlib. Use a full JSON Schema validator in development to check the contract and parity fixtures; do not build a general JSON Schema interpreter inside DuckTerm. This proposal only supplies the schema and illustrative data.

## Migration and delivery sequence

1. First add structured content and validation to the existing store. Preserve each legacy instruction verbatim and retain its old ID in provenance; assign an immutable UUID once. `heading` becomes category, `text` becomes instruction, and optional metadata remains absent when unknown. Generated titles need review if they alter meaning.
2. Preserve status, rejected tombstones, source information, notes, and historical evidence counts in the migration archive. Do not fabricate event references, approval identities, or explanations. A migration acceptance records a trusted decision for the exact imported records.
3. Map `all` to everyone in the existing **directory**, never globally. Map runtime/model strings to a harness plus an explicit legacy model selector. Retain host and canonical directory binding. Review any proposed move to sidebar-folder or global scope.
4. Before migration, compare generated Markdown with the actual AGENTS.md. This checkout already differs from its JSON store, including terminology and additional rules. Present a reconciliation diff and preserve both originals. Do not overwrite one source merely because a banner calls it generated.
5. Make JSON the authoritative store after reconciliation. Write exports via temporary files and atomic replacement; since two files are not a single transaction, store revision/hash information and detect interrupted or external writes. Regenerate or reconcile explicitly instead of claiming drift is impossible.
6. Add global/folder resolution and harness delivery with focused tests. Add named-agent and session scope only after their identity and delivery semantics are implemented. Until then they may be drafted, but activation reports unsupported capability.

Acceptance cases: legacy text retained verbatim; `all` does not become global; duplicate wording does not collide IDs; a stale edit is rejected; candidate/rejected/retired rules do not become effective; missing scope targets never broaden scope; agent/session rules do not enter another session's export; fork and resume behave as specified; replacement cycles fail; renderer preserves exceptions and does not execute examples; source/export drift is surfaced; approved revisions are the ones delivered.

The first implementation should improve content structure and authoring. Broader scope delivery is a separate capability, even though the schema describes its eventual shape.
