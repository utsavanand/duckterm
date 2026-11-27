---
name: review
description: >
  Review a diff or a pull request against its spec: correctness, security, tests, slop and
  danger zones, each finding with a confidence score, ending in approve, revise or reject.
  Use before shipping; the pr-review, build, ship and issue-to-merge flows run it.
argument-hint: "[branch, pull request number or URL]"
user-invocable: true
allowed-tools:
  - Read(*)
  - Grep(*)
  - Glob(*)
  - Write(.autopilot/**)
  - Bash(git diff *)
  - Bash(git log *)
  - Bash(git show *)
  - Bash(git rev-parse *)
  - Bash(git merge-base *)
  - Bash(gh pr view *)
  - Bash(gh pr diff *)
---

## What to review

$ARGUMENTS

When a flow runs this skill the Inputs JSON names the change: `pr` (a number or
URL: read `gh pr view <pr>` for title and description and `gh pr diff <pr>`
for the diff; without the GitHub CLI, say so and stop), or `diff` (a file). It may also name `spec`, the spec the change
was built from. Invoked directly, review the staged diff, else the working
tree diff, else `git diff <branch>...HEAD` when `$ARGUMENTS` names a branch.
An empty diff means there is nothing to review: say so and stop.

## Context to read first

- The spec, when given or found at `.autopilot/spec.md`: the Requirements and
  Success criteria are what the change is judged against.
- `AGENTS.md` or `CLAUDE.md` at the project root, if present.
- `DANGER-ZONES.md` at the project root, if present.
- For every changed file, enough of the unchanged surroundings to judge the
  change in place, and its tests.

## Checklist

Work through each area; skip one only when the diff gives it nothing to
touch, and say so in the report.

- **Correctness.** Does the change do what the spec says, for the boundaries
  and errors as well as the happy path? Is anything half done or left as TODO?
- **Security.** Input that reaches a shell, a query, a file path or a URL;
  secrets in code or logs; auth checks removed or skipped; new network calls.
- **Tests.** Is each requirement asserted by a test that would fail if the
  behaviour broke? Tests that only check existence, only check a mock, or
  snapshot trivial output do not count.
- **Slop.** Abstractions with one implementation; error handling for
  impossible states or catch-log-rethrow; comments that restate the code;
  "future-proofing" with no named trigger; documentation that uses adjectives
  where facts belong.
- **Surgical.** Every changed line traces to the request. Reformatting,
  renames and drive-by refactors are findings.
- **Danger zones.** A changed file that `DANGER-ZONES.md` lists must honour
  its Rule; `@agent-skip` blocks must be untouched.

## Findings

Each finding: `file:line`, one-line title, what is wrong, the fix, and a
confidence from 1 to 10:

| Confidence | Meaning | In the report |
|---|---|---|
| 9-10 | visible in the diff, demonstrable | top, no caveat |
| 7-8 | a known anti-pattern with no mitigating context | normally |
| 5-6 | likely, depends on code outside the diff; name the assumption | with "(medium confidence)" |
| 3-4 | possible, would need more code to confirm | appendix |
| 1-2 | speculation or taste | left out |

Order by confidence, then severity. Prefer three findings that matter over
twelve that do not.

## Verdict

End with exactly one of these choices and a one-line reason:

- `approve` — nothing blocks a merge
- `revise` — fixable issues, listed in the findings
- `reject` — the change should not land in this form: it does not do what the
  spec says, or it is unsafe

A finding at confidence 9-10 in Correctness or Security means at least
`revise`. Style alone never makes `reject`.

## Output

Write the findings file to the path the runner names, or to
`.autopilot/review.md` when invoked directly: the verdict and reason first,
then findings by tier, then the appendix, then the areas skipped and why. In
the terminal, give the verdict, the counts by tier, and the findings at
confidence 5 and above. Do not change any other file.
