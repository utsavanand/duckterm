---
name: fix-ci
description: >
  Fix what made a pull request's CI fail, from the failing checks' log: find the cause, make
  the smallest change that fixes it, and run the failing check locally where possible. Use
  when CI fails on a branch; the autopilot runs it between CI and merge.
argument-hint: "[log file or pull request]"
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

## The failure

$ARGUMENTS

When a flow runs this skill, the Inputs JSON carries `log`: each failed check
with its title, summary and annotations (file, line, message). The branch is
the change that failed; `git log` shows what it did.

## Fix it

1. Read every failed check before changing anything. Group the failures by
   cause: one cause often fails several checks.
2. For each cause, open the file and line the log names, and the code the
   branch changed near it. Say what broke and why.
3. Make the smallest change that fixes the cause. Fix the code, not the
   check: never skip, delete or loosen a test, a lint rule or a type check to
   make it pass, and never edit CI workflow files (pushing them is refused).
4. Run the failing check's command here when the tools are installed. When
   they are not, say so and rely on reading the code.

If the failure is not caused by this branch (a flaky test, an outage, a
failure that is also on the default branch), change nothing and say so.

## Output

End with one paragraph: each cause, the fix, and how you checked it.
