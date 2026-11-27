---
name: ship
description: >
  Run the tests and lint, read the diff once more, commit with a message that says why, and
  open the pull request. Use when the work is reviewed and verified; the ship flow runs it
  last, after review and QA agreed.
argument-hint: "[commit message or 'pr']"
user-invocable: true
allowed-tools:
  - Read(*)
  - Grep(*)
  - Glob(*)
  - Bash(git *)
  - Bash(npm test *)
  - Bash(npm run *)
  - Bash(npx *)
  - Bash(pytest *)
  - Bash(go test *)
  - Bash(cargo test *)
  - Bash(gh pr *)
---

## Intent

$ARGUMENTS

As the last step of the ship flow there are no arguments: the review and QA
verdicts are in, so run the pipeline and open the pull request. Invoked
directly, `pr` means commit and open the pull request; any other text is the
commit message.

## State

`git status --short`, `git diff --cached --stat`, `git diff --stat`. Read the
newest `.autopilot/review.md` and `.autopilot/qa.md` if present: a `reject`
or `fail` there stops the pipeline until a person says otherwise.

## Pipeline

1. **Tests.** Run the project's test command bare and read the result line.
   On failure, fix it if it is yours and plain; otherwise stop and report.
   Never commit red.
2. **Lint.** Run it when the project has one; apply auto-fixes only.
3. **Read the diff once more.** Correctness, a secret or credential, a
   half-done piece, a file listed in `DANGER-ZONES.md`. Fix what is plain;
   stop and ask on anything that needs a person.
4. **Stage and commit.** Stage the files that belong to the change. Never
   `.env`, credentials, build output, `node_modules`, or `.autopilot/`. One
   commit per logical change. The message says the problem, the fix and how it
   was tested, not a list of files; use `$ARGUMENTS` when it is a message.
5. **Pull request**, when asked or when on a feature branch: push the branch
   and `gh pr create` with a title under 70 characters and a body of what
   changed, why, and how to test it. On the default branch, create a branch
   first; never push to the default branch.

## Rules

- A failing test that is not yours is a stop, not a skip.
- A danger-zone file in the diff is named in the commit body and the PR.
- Force pushes and history rewrites need a person's word.
