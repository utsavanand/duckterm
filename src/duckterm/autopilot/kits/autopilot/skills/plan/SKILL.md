---
name: plan
description: >
  Turn an approved spec into a build plan: the design decisions, then Acts of tasks with
  dependencies, sizes, cycle budgets and the check that proves each one. Use after a spec is
  written and before coding; the build and ship flows run it and pause for approval.
argument-hint: "[spec file]"
user-invocable: true
allowed-tools:
  - Read(*)
  - Grep(*)
  - Glob(*)
  - Write(.autopilot/**)
  - Bash(git log *)
---

## Input

$ARGUMENTS

A plan is made from a spec, never from a sentence. When a flow runs this skill
the Inputs JSON names the `spec` file. Otherwise `$ARGUMENTS` names it, or the
newest `.autopilot/spec.md` is used. With no spec anywhere, stop and say that
`/spec` has to run first.

## Context to read first

- The spec, in full.
- `AGENTS.md` or `CLAUDE.md` at the project root, if present.
- `DANGER-ZONES.md` at the project root, if present.
- The files the spec names, enough to see their shape and tests.

## Design

Before the tasks, write the decisions the tasks depend on:

- **Constraints.** Scale, team, availability, data sensitivity, cost of
  failure: take each from the spec where stated, otherwise state the
  assumption in one line. Do not design for a scale the spec does not name.
- **Approach.** The simplest structure that meets the requirements, and the
  one or two alternatives you rejected with the reason.
- **Challenge test.** For every new component or dependency: what breaks if we
  do not have it? If the answer is "nothing for months", leave it out and
  write down the trigger that would bring it back.

## Acts

An Act is a slice that leaves the codebase working and demonstrable. Three
Acts is typical; one is fine. Each Act has:

- **Goal.** One sentence, in user terms.
- **Tasks.** A table: `id` (`2.3` is task 3 of Act 2), `description`, `depends
  on`, `size` (S: one file, M: two or three files, L: more or a migration),
  `cycle budget` (S 2, M 3, L 5 write-run-adjust cycles before the agent stops
  and asks), `check` (the test or command that proves it).
- **Exit criteria.** The checks that must pass before the next Act starts.

Rules: a task touches what its check needs and nothing else; two tasks that
can run in parallel say so with an empty `depends on`; a task with no check is
not a task, give it one or merge it. Tasks that touch a file listed in
`DANGER-ZONES.md` quote that file's Rule.

## When to stop and ask

Interactive: ask once when the spec is contradictory or a constraint is
missing and matters. As a flow step the run is non-interactive: do not ask.
Choose the smaller design, mark the assumption, and let the approval gate
decide; the flow shows the plan to a person before any code is written.

## Output

Write `plan.md` to the path the runner names, or to `.autopilot/plan.md` when
invoked directly. Sections in this order: Constraints, Approach, Challenge
test, then one section per Act. Do not change any other file.
