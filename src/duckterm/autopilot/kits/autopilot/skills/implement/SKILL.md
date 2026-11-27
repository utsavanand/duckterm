---
name: implement
description: >
  Build what a plan or a spec says: test first, one task at a time, within the task's cycle
  budget, touching only the files the task names. Use after /plan for each Act in order, or
  straight from a spec for a small fix; the build, ship and issue-to-merge flows run it.
argument-hint: "[act number or task id, e.g. 2 or 2.3]"
user-invocable: true
allowed-tools:
  - Read(*)
  - Grep(*)
  - Glob(*)
  - Write(*)
  - Edit(*)
  - Bash(git status *)
  - Bash(git diff *)
  - Bash(git log *)
  - Bash(npm test *)
  - Bash(npm run *)
  - Bash(npx *)
  - Bash(pytest *)
  - Bash(go test *)
  - Bash(cargo test *)
---

## Target

$ARGUMENTS

When a flow runs this skill, the Inputs JSON carries `plan` (a file), or
`spec` alone for a small fix, and may carry `findings` from a review that sent
the work back. Invoked directly, an Act number builds every task of that Act
in dependency order, a task id builds that task alone, and no argument takes
the first Act whose exit criteria are not met.

## Context to read first

- The plan: the Inputs' `plan`, else the newest `.autopilot/plan.md`. Without a
  plan, the spec's **Change** and **Test** sections are the task list: one task
  per file, the spec's test as the check. Without either, stop and say that
  `/spec` has to run first; do not invent the work.
- The spec the plan was made from.
- `findings`, when given: each finding is a task to do before anything else.
- `AGENTS.md` or `CLAUDE.md` at the project root, if present.
- `DANGER-ZONES.md` at the project root, if present.
- The working rules this MetaHarness installs, for the Stuck format.

## Procedure, per task

1. **Restate the task.** Quote its row: description, depends on, size, cycle
   budget, check. If a dependency is not done, stop and say which.
2. **Write the check first.** The failing test, in the project's own
   framework and conventions, before the code. A task whose check cannot be
   written as a test gets a command that proves it instead; it does not get
   skipped.
3. **Make the smallest change that passes.** Touch only the files the task
   needs. Match the existing style. No refactor of adjacent code, no comments
   that restate the code, no abstraction for a single use.
4. **Run the whole test suite and the lint**, not only the new test. Fix what
   your change broke.
5. **Danger zones.** If a touched file is listed in `DANGER-ZONES.md` or
   carries an `@danger` or `@agent-ask` marker, stop and quote the Rule
   before going on. `@agent-skip` blocks stay as they are.
6. **Count the cycles.** One write-run-adjust-rerun is one cycle. When the
   task's budget is spent, or earlier when you can see you are stuck, stop and
   report in the Stuck format. Never "one more try".
7. **Do not commit.** The ship skill commits after review.

## Report

For each task: the files changed, the checks that now pass, what remains for
the Act's exit criteria, and any assumption you made. Write it to
`.autopilot/implement.md` when invoked directly; as a flow step the diff is
the result and the report is your final message.
