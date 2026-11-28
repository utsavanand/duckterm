---
name: security
description: >
  Review a change for security: what it lets an attacker do, what it exposes, and what it
  touches, ending in approve, revise or reject and a classification of the change as
  written. Use before shipping; the issue-to-merge flow runs it beside QA.
argument-hint: "[branch or diff file]"
user-invocable: true
allowed-tools:
  - Read(*)
  - Grep(*)
  - Glob(*)
  - Write(.autopilot/**)
  - Bash(git diff *)
  - Bash(git log *)
  - Bash(git show *)
---

## What to review

$ARGUMENTS

When a flow runs this skill, the Inputs JSON carries `diff` (a file with the
change) and may carry `spec`. Read the whole diff, then the code around each
hunk: a change is only as safe as what calls it.

## Look for

- Input that reaches a shell, a query, a file path, a template or a network
  call without validation.
- Secrets or tokens written to logs, files, URLs or error messages.
- Authentication or permission checks removed, skipped or made optional.
- New dependencies, new network destinations, widened file or process access.
- CI workflow changes, and anything that runs on install or build.
- Changes to prompts, skills or agent instructions (AI behaviour).
- Stored data: schema changes, migrations, deletions.

Each finding: file and line, what an attacker or a mistake could do, how
likely, and the fix. Mark a finding `needs_human` when only a person can judge
it (a trade-off, a policy). Do not report style.

## Classify the change as written

Write `classification.json` from the diff itself, not from the spec, with the
same fields the spec step uses: `surfaces`, `user_visible`, `regression`
(`level`, `features`), `security` (`level`: `none` or `touches`, `areas`),
`ai` (`deterministic` or `model_dependent`), `data` (`none`, `schema`,
`migration`, `deletion`), `size` (`small`, `medium`, `large`). Pick the higher
value when unsure.

## Verdict

- `approve`: nothing found that should block a merge
- `revise`: fixable findings, listed for the build step
- `reject`: the change should not be made in this form

Do not change any file outside the output path.

## Output

Write `security.md` (the verdict, the reason and the findings) and
`classification.json` to the path the runner names, or to `.autopilot/` when
invoked directly. End with exactly one of `approve`, `revise`, `reject` and a
one-line reason.
