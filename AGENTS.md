# AGENTS.md — duckterm

Shared instructions for every agent working in this folder. Blocks are typed —
see docs/agents-template.md for the format. A `scope` other than `all` means
the rule applies only to that agent runtime; skip blocks scoped to a runtime
that isn't you.

## Shipping

<!-- rule {"id":"no-piped-gates","scope":"all","status":"active","source":"retro","evidence":3,"added":"2026-09-21"} -->
- Never pipe the gate or tests through grep/tail/head — pipelines report the
  filter's exit code, not the test's. Run `scripts/gate.sh` bare; read the
  log at /tmp/duckterm-gate.log. Piped gates shipped two broken releases
  (RETRO.md).

<!-- rule {"id":"ship-end-to-end","scope":"all","status":"active","source":"manual","evidence":1,"added":"2026-09-21"} -->
- A fix isn't done until shipped end-to-end: commit → gate → bump version →
  tag → fresh-worktree build (`scripts/build_package.sh`) → GitHub release
  with ABSOLUTE asset paths → `pipx install --force` → relaunch
  RubberTerm.app → curl the dashboard for 200.

<!-- rule {"id":"worktree-builds-no-import","scope":"all","status":"active","source":"retro","evidence":1,"added":"2026-09-21"} -->
- Build releases from a fresh worktree at the tag with its own venv. Never
  import duckterm inside build scripts — read the version with sed. An import
  once aborted the build mid-bundle and shipped a partial app.

## Parallel sessions

<!-- rule {"id":"contested-files","scope":"all","status":"active","source":"retro","evidence":2,"added":"2026-09-21"} -->
- Other agent sessions work in this repo concurrently. Never stage whole
  contested files and never `git add -A`. Don't format, lint-fix, or delete
  another session's untracked WIP — exclude it and move on.

<!-- rule {"id":"verify-committed-tree","scope":"all","status":"active","source":"retro","evidence":2,"added":"2026-09-21"} -->
- Before releasing, verify the COMMITTED tree imports and compiles in a fresh
  worktree. The working tree lies when another session has WIP.

## Conventions

<!-- rule {"id":"commit-format","scope":"all","status":"active","source":"correction","evidence":2,"added":"2026-09-21"} -->
- Commit messages: problem / fix / how-tested. No Co-Authored-By trailers.

<!-- rule {"id":"zero-deps","scope":"all","status":"active","source":"manual","evidence":1,"added":"2026-09-21"} -->
- The server stays zero-dependency Python stdlib. Don't add pip deps to solve
  a problem stdlib can solve.

<!-- rule {"id":"meta-harness-term","scope":"all","status":"active","source":"correction","evidence":1,"added":"2026-09-21"} -->
- The user-facing term is "meta-harness" — never "suite" or "overlay" in UI
  or docs.

<!-- rule {"id":"retro-after-fix","scope":"all","status":"active","source":"correction","evidence":1,"added":"2026-09-21"} -->
- Append a lesson to RETRO.md after every real fix, newest first.

## Testing hygiene

<!-- rule {"id":"flag-test-sessions","scope":"all","status":"active","source":"correction","evidence":1,"added":"2026-09-21"} -->
- Probe/test sessions must carry test:true and be deleted afterwards. Never
  leave unflagged test data in the DB.

<!-- rule {"id":"read-full-test-output","scope":"all","status":"active","source":"retro","evidence":2,"added":"2026-09-21"} -->
- Read complete test output (or the exit code) before claiming green —
  tail-truncated output has been misread as a pass twice.

## UI changes

<!-- rule {"id":"verify-visuals-at-size","scope":"all","status":"active","source":"correction","evidence":3,"added":"2026-09-21"} -->
- Verify visual changes at real size with real data before shipping — the
  user reviews screenshots/previews before implementation for anything
  visual. Prefer removing a struggling UI element over patching it; note the
  re-home idea in TODO.md.

## Runtime-specific

<!-- rule {"id":"claude-context-budget","scope":"claude-code","status":"active","source":"manual","evidence":1,"added":"2026-09-21"} -->
- fable-5 sessions have a 1M-token context window — don't compact or trim
  summaries preemptively at 200k-window thresholds.

<!-- rule {"id":"codex-approvals-in-terminal","scope":"codex","status":"active","source":"manual","evidence":1,"added":"2026-09-21"} -->
- Answer approval prompts in your own terminal promptly — codex approvals
  fire no hooks, so nothing else will surface them to the user.
