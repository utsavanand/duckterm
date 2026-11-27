---
name: test
description: >
  Write tests for a target: unit by default, integration on request, matching the
  project's framework and conventions, and run them. Use after implementing something,
  when a file has no tests, or when a bug needs a reproducing test first.
argument-hint: "[file, function or feature] [--unit|--integration]"
user-invocable: true
allowed-tools:
  - Read(*)
  - Grep(*)
  - Glob(*)
  - Write(*)
  - Edit(*)
  - Bash(npm test *)
  - Bash(npm run *)
  - Bash(npx *)
  - Bash(pytest *)
  - Bash(go test *)
  - Bash(cargo test *)
---

## Target

$ARGUMENTS

`--integration` tests the target through its real neighbours (database,
files, HTTP); otherwise unit. With no target, ask which file, function or feature to test,
in one question, and wait.

## Match the project

- Find the existing tests (`*.test.*`, `*.spec.*`, `test_*`, outside
  `node_modules`) and read one near the target: its framework, fixtures,
  naming and assertion style are the ones to use.
- Read the test command from `package.json` scripts, `pyproject.toml`, a
  `Makefile` or the CI config, and run the suite once before writing so you
  know the baseline.
- Read the newest `.autopilot/spec.md` if there is one: its Requirements and
  Success criteria are the behaviours to assert.

## What a test is

Each test asserts one specific behaviour with a specific value, side effect
or error. These do not count and are not written:

- Assertions that something exists or is truthy (`toBeTruthy`, `is not None`
  alone).
- Tests where the mock is the only thing under test: the mock returns X, the
  test asserts X.
- Snapshot tests of trivial output.
- Tests of the framework ("does the component render").
- Copies of one test with a changed literal: use a parameterised test.

Ask of every test: would it fail if the behaviour broke? If not, delete it.

## Procedure

1. List the behaviours of the target from its code and the spec: the happy
   path, each boundary, each error, each permission or state that changes the
   result.
2. Write one test per behaviour, named for the behaviour, next to the
   existing tests for that module.
3. Run the suite. A new test that fails against current code either found a
   bug (report it, leave the test red, do not change the code) or is wrong
   (fix the test).
4. Do not commit.

## Report

Behaviours covered, tests added by path, the suite's result line, and any red
test that found a real bug.
