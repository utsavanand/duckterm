---
name: spec
description: >
  Turn an issue, a request or vague requirements into a short technical spec: the problem,
  the requirements, the success criteria, the change file by file, the test that proves it,
  the risk. Use before planning or fixing anything; the issue-to-merge flow runs it first.
argument-hint: "[issue number, URL or requirements]"
user-invocable: true
allowed-tools:
  - Read(*)
  - Grep(*)
  - Glob(*)
  - Write(.autopilot/**)
  - Bash(git log *)
  - Bash(gh issue view *)
---

## What to spec

$ARGUMENTS

When a flow runs this skill, the Inputs JSON carries `issue` (number, title, body,
labels and URL) and the runner names the output path. Otherwise `$ARGUMENTS` is
the request; an issue number or URL is read with `gh issue view <id> --json
title,body,labels,url`.

## Context to read first

- `AGENTS.md` or `CLAUDE.md` at the project root, if present.
- `DANGER-ZONES.md` at the project root, if present.
- The code the request concerns: find the files and functions by name and by
  the terms the reporter used. Quote real paths in the spec; never guess at a
  module you did not open.
- Prior specs under `.autopilot/`, newest first, so this one builds on them
  instead of repeating them.

## Write the spec

One file with these sections, in this order:

1. **Problem.** What is wrong or missing, in the requester's terms, and where
   in the code it happens (file and function). If the request does not
   describe something you can find in the code, say so here and stop after
   this section.
2. **Requirements.** Numbered, each testable. Mark anything you inferred
   rather than read as *(assumed)*.
3. **Success criteria.** How a reviewer tells the work is done: observable
   behaviour, commands that pass, numbers that change.
4. **Change.** The change you would make, file by file, smallest first. No
   refactor of nearby code; every line traces to a requirement.
5. **Test.** The existing test to extend or the new test to add, by path, and
   what it asserts.
6. **Risk.** What else the change could break and how the test guards it.
7. **Out of scope.** What the request mentions that this spec deliberately
   leaves out, and why.

Keep it under two screens. A spec that is long is a spec that was not decided.

## When something is undecidable

Interactive: ask once, grouping every question, then write. As a flow step the
run is non-interactive: do not ask. Pick the smaller interpretation, record it
under Requirements as *(assumed)*, and list the alternatives under Out of scope
so the reviewer can object.

## Output

Write `spec.md` to the path the runner names, or to `.autopilot/spec.md` when
you were invoked directly, creating the folder if needed. Do not change any
other file.
