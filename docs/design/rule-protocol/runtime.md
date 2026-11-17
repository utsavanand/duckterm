# Structured rules: implementation contract

Status: implemented in this branch; not released. The [design proposal](README.md) describes broader scope and delivery work. This document describes the current wire format and its limits.

## Storage and compatibility

`.duckterm-rules.json` is the catalogue in a filesystem directory. Version 2 keeps the existing `text`, `heading`, and string `scope` fields so existing instructions and IDs survive unchanged. It adds optional `title`, `explanation`, `when`, `examples`, `exceptions`, `revision`, and `source_refs`. New accepted rules get UUIDs. Existing IDs remain stable through edits. See [stored-rule.schema.json](stored-rule.schema.json) for the rule record contract. The adjacent `rule.schema.json` remains a future design, not the on-disk format.

The catalogue contains `schema_version: 2`, `rules`, `proposals`, `capture_fingerprint`, and `rendered_sha256`. Reads support version 1; the next reviewed save writes version 2. Legacy titles and explanations stay empty until edited. Unknown version-2 rule fields and malformed records fail validation rather than disappearing on save. Retired and rejected records remain available.

The dashboard edits the catalogue, then renders active rules to AGENTS.md. It includes instructions, explanations, conditions, exceptions, and complete examples. Source references and proposal history stay outside the instruction export. Harness-specific session instructions also include the complete rule body.

## Reconciliation protocol

Where rule suggestions are available, both manual additions and observed owner corrections go through the same comparison prompt, `src/duckterm/prompts/rules_reconcile.txt`. Its input contains original observations with stable references, current rules including rejected records, and prior proposal history. Checkpoint summaries are never counted as new evidence.

The response is exactly `{ "decisions": [...] }`. Each decision has `claim`, `memory_kind`, `source_refs`, `action`, `reason`, `target`, `changes`, and `context`. Classification distinguishes procedural, episodic, factual, noise, and uncertain observations.

| Action | Effect after review and Save |
| --- | --- |
| `create_rule` | Add a distinct instruction with a new UUID. |
| `revise_rule` | Update specified author fields on the existing ID. |
| `add_example` | Append one concrete example to the existing instruction. |
| `attach_evidence` | Add distinct original references without repeating instruction text. |
| `no_change` | Record that the existing guidance already covers the observation. |
| `retain_context` | Keep an observation in the review history; do not install a rule. |
| `needs_review` | Preserve an unresolved conflict or ambiguous scope for the owner. |
| `ignore` | Record a noise classification without changing memory. |

Changes to an existing rule name its ID and base revision. Create/revise/example/evidence proposals require procedural classification. The model cannot set approval status. A proposal is content-addressed; pending, accepted, and rejected review states are stored separately. The owner may select a harness for a new rule, recorded as `scope_override` without rewriting the original suggestion. Undo review is available until Save. Reviewed history cannot be silently rewritten or deleted.

Manual editing remains available in editions without rule suggestions. Those editions make no model request; the owner reviews a new instruction directly, with an exact-text duplicate check. Background capture respects the same capability.

This uses the existing dashboard authentication and explicit review workflow. It does not add a signed approval boundary: processes with local filesystem or existing API access retain those capabilities. Source attribution and semantic classification depend on the observations and model; strict JSON validation alone cannot establish that prose is correct.

## Scope in this implementation

All records remain bound to their filesystem directory. `scope: "all"` means all harnesses there; it never means global. Supported harness names are `codex`, `claude-code`, `copilot`, and `opencode`. Existing `harness/model-fragment` matching remains compatible. Harness labels in a shared AGENTS.md are advisory; the session-instruction path filters matching records.

The page filters by harness, status, and search text. Global collections, sidebar-folder resolution, reusable-agent targeting, and session-specific delivery are not implemented. The UI says so. Those capabilities require the identity and delivery work described in the design before they can be enabled truthfully.

## Writes and failure behavior

A catalogue version hashes both source files. Saving stale content fails. Writers use an advisory file lock and atomic file replacements. The two replacements are not a filesystem transaction; subsequent reads detect a mismatched export, including interrupted writes and external edits to either file. The owner must compare and explicitly replace a divergent AGENTS.md; a backup preserves the original.

Handwritten AGENTS.md files remain readable without an automatic import that could lose instructions. No live repository file is migrated merely by opening the page. Background checkpoint capture runs only for existing typed catalogues without drift, compares original-source fingerprints, and saves pending proposals without rewriting AGENTS.md. Backend or validation errors leave approved rules intact.

“Saved” means written to disk, not confirmed loaded by every running agent. The UI explicitly says changes apply when instructions are next loaded. Episodic observations do not automatically create another checkpoint; checkpoint prompts continue to summarize events separately from procedural rule capture.
