"""Skill kits: the declarative package format (docs/specs/skill-kit-protocol-spec.md).

manifest  — read `skill-kit.toml` into a Kit with typed components
validate  — authoring rules; parse-and-inspect only, never runs kit code
adapters  — per-harness table: where each component kind lands, or why not
plan      — a dry run: every file and config edit, conflicts, result rows
install   — apply a plan, write the record, uninstall from the record, verify
shim      — the hook shim written into the project so one script serves every harness

Everything here is stdlib only and works without the DuckTerm server, which is
what lets `duckterm kit …` run from a bare clone.
"""
