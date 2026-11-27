---
name: qa
description: >
  Verify a change works: run the project's tests, lint and build, then exercise the running
  app when a URL is given, and answer pass or fail with a report. Use after implementing,
  before shipping; the ship flow runs it beside the review.
argument-hint: "[url]"
user-invocable: true
allowed-tools:
  - Read(*)
  - Grep(*)
  - Glob(*)
  - Write(.autopilot/**)
  - Bash(git status *)
  - Bash(git diff *)
  - Bash(npm test *)
  - Bash(npm run *)
  - Bash(npx *)
  - Bash(pytest *)
  - Bash(go test *)
  - Bash(cargo test *)
  - Bash(curl -sf *)
---

## Target

$ARGUMENTS

When a flow runs this skill the Inputs JSON may carry `url` (the running app),
`diff` (the change under test) and `spec`. Invoked directly, `$ARGUMENTS` is
the URL if any. Without a URL, QA is the checks alone; say so in the report
rather than pretending the app was exercised.

## Context to read first

- The spec, when given or found at `.autopilot/spec.md`: its Success criteria
  are the pass conditions.
- The diff, to know which pages, routes or commands the change touches.
- `AGENTS.md` or `CLAUDE.md` at the project root, for the project's own test
  and run commands.

## Checks

1. **Tests.** Run the project's test command, bare, and read the whole result
   line. Never pipe it through a filter: the filter's exit code would replace
   the test's.
2. **Lint and types.** Run the lint and type-check commands when the project
   has them.
3. **Build.** Run the build command when the project has one.
4. **App**, when a URL is given: confirm it answers (`curl -sf <url>`). Then
   exercise what the diff touched. Use a browser tool when the harness has
   one (a Playwright or Chrome tool): open the entry page and each touched
   page at desktop and phone width, perform the actions the spec names, and
   read the console for errors after each. Without a browser tool, request
   the touched routes with `curl` and check status and body.
5. **Success criteria.** Tick each criterion of the spec with the evidence
   that shows it: a command and its result line, a page and what it showed.

Do not change any file. A failing check is reported, not fixed here; the
implement step fixes it on the next loop.

## Findings

Each finding: where (page, route, command), what happened, what was expected,
severity: `critical` (crash, blank page, data loss, failing test), `high`
(dead control, silent failure, criterion unmet), `medium` (visual defect,
console warning, slow with no feedback), `low` (nit). Say what you observed,
with the evidence, before what you assume.

## Verdict

End with exactly one of these choices and a one-line reason:

- `pass` — the checks are green and every success criterion is met
- `fail` — a check failed or a criterion is unmet; the report says which

A single `critical` or `high` finding means `fail`.

## Output

Write the report to the path the runner names, or to `.autopilot/qa.md` when
invoked directly: the verdict and reason, the commands run with their result
lines, the pages exercised, the findings by severity, and the criteria table.
