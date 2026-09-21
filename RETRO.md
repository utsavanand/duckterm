# Retro — lessons from real breakage

Append-only. One entry per issue we actually hit: what broke, the root cause,
and the rule that prevents the recurrence. Newest first.

## 2026-09-21 — A fresh browser hid a stale native dashboard
**Broke:** the browser showed six connectors while the user's open native window
still showed three. The file editor also sat below verbose metadata and branches.
**Cause:** verification opened a fresh page instead of checking the existing
native window; secondary details displaced the main file action.
**Rule:** load the shipped assets in the native app as part of verification.
Keep the main action above metadata, collapse secondary lists, and verify the
committed build independently of concurrent work before installing it.

## 2026-09-20 — Masked a gate again, hours after writing the rule
**Broke:** a commit gate ran `pytest | tail -1` — the pipeline reported
tail's exit code, the failure scrolled past, and the commit landed anyway
(the failure was the parallel session's WIP, but the gate didn't know that).
Same mistake as the 0.4.18 grep-mask, same day, same author.
**Cause:** hand-composing gate pipelines ad hoc every time invites the same
slip; a written rule doesn't change muscle memory.
**Rule:** rules that fight muscle memory must become TOOLING. scripts/gate.sh
now runs the whole gate with set -e and zero output filtering — call it bare,
the exit code is the verdict. It caught a formatting drift on its first run.

## 2026-09-20 — Two broken releases from one shared import hunk
**Broke:** 0.4.17 and 0.4.18 both crashed at server startup (ModuleNotFound /
ImportError) and had to be retracted; the app was down until rollback.
**Cause:** committing "only my hunks" from a file another session was editing
— but imports cluster, so one diff hunk carried my import AND two of theirs,
whose modules stayed uncommitted. Twice. The wheel smoke check added after
the first failure DID catch the second — and was defeated by piping the
build script through grep, which reported grep's exit code, not the script's.
**Rule:** after any selective staging, verify the COMMITTED tree, not the
working tree: fresh worktree, pip install, import the entrypoints — before
tagging. And never pipe a gating command through a filter; capture its exit
code first, filter its saved output after.

## 2026-09-20 — Delete/rename dialogs silently dead in the Mac app
**Broke:** the folder ✕ (window.confirm) and rename/new-folder prompts
(window.prompt) did nothing in RubberTerm.app — confirm returned false,
prompt returned null.
**Cause:** WKWebView no-ops all JS dialogs unless the app implements
WKUIDelegate. Third app-shell gap of this kind (menu key equivalents, copy
validation, now dialogs) — and Chromium e2e can never catch any of them.
**Rule:** a WKWebView shell needs the full trio wired on day one: main menu,
clipboard bridge, WKUIDelegate dialogs. Browser-based e2e proves the PAGE,
not the SHELL — after changing app-shell code, walk the dialog/clipboard/
shortcut paths in the actual app.

## 2026-09-20 — Digest turned a one-off remark into a personality trait
**Broke:** the "working together" digest characterized the user from single
data points — one 'too wordy' comment became "iterates on naming rapidly,"
one scoping decision became "prefers simple scope."
**Cause:** the summarizer prompt asked for observations but set no evidence
bar, so the model generalized from n=1 and padded the list to look thorough.
**Rule:** any LLM feature that makes claims about a PERSON needs an explicit
evidence bar in the prompt (recurrence or an outright statement), behavior
over personality, and "an empty list is better than a stretched one."
Review the first real outputs with the user — they spot overreach instantly.

## 2026-09-20 — The e2e suite spent an afternoon testing a stale UI bundle
**Broke:** two new Playwright tests failed mysteriously (pass alone, fail in
suite); the served dashboard didn't contain the code under test.
**Cause:** `dashboard_dir()` preferred the packaged copy
(src/duckterm/dashboard, frozen at the last release build) over web/dist, so
dev servers and e2e silently served a bundle three releases old. Compounded
by reading `... | tail -3` output and mistaking a truncated failure list for
a pass.
**Rule:** the dev checkout must always serve the freshest build (dist wins
over the packaged artifact), and the e2e suite must BUILD what it tests, not
trust what's lying around. Never judge a test run from truncated output —
read the pass/fail summary line itself.

## 2026-09-20 — Copy/paste still dead in the Mac app after adding menus
**Broke:** ⌘C/⌘V did nothing in RubberTerm.app even with a proper Edit menu.
**Cause:** WKWebView enables the standard `copy:` menu item only when the DOM
has a selection — xterm renders selection on canvas, so the item stayed
disabled and the key equivalent was inert. Fixed by bridging Copy/Paste menu
actions into the page (`__rtCopy`/`__rtPaste` globals + NSPasteboard).
**Rule:** in a native web-view shell, don't assume responder-chain editing
selectors work for canvas-rendered UI — bridge explicitly. And make the web
view `isInspectable` from day one so the next web-in-native bug is debuggable.

## 2026-09-20 — Declared Shift+Enter "shipped" before testing the real path
**Broke:** user reported Shift+Enter still submitting after the fix shipped.
**Cause:** the fix was validated by reasoning (unit-level) only; no test drove
a real browser keystroke through WS → tmux → agent. The follow-up e2e and a
live probe against a codex TUI were written only after the bug report.
**Rule:** a fix for an interaction bug ships WITH a test at the outermost
layer that was broken (here: a Playwright keypress asserting the byte reached
the agent). "The code now sends the right byte" is a hypothesis until the
end-to-end test passes.

## 2026-09-20 — Codex sessions showed an empty Messages tab
**Broke:** Messages view silently blank for codex sessions.
**Cause:** `/sessions/:key/messages` was hardwired to claude-code with an
`isinstance` check; every other harness returned `[]` with no signal.
**Rule:** a per-harness capability belongs on the harness contract (default =
unsupported), not behind `isinstance` in an endpoint. And "unsupported" should
be visible in the UI, not indistinguishable from "no data".

## 2026-09-20 — Shift+Enter submitted instead of inserting a newline
**Broke:** multi-line prompts impossible in the browser terminal.
**Cause:** xterm sends plain `\r` for Shift+Enter — the harness can't tell it
from Enter.
**Rule:** key chords that terminals don't encode distinctly must be intercepted
client-side and translated to a keystroke the TUI understands (LF/Ctrl+J here).
Test with the real harness, not just the shell.

## 2026-09-20 — Mac app launched to a blank white window
**Broke:** first cold launch (app starts its own server) showed a blank page.
**Cause:** the WKWebView loaded the URL exactly once, racing the server it was
itself starting; a failed local load has no retry.
**Rule:** anything that loads from a server it also starts must retry until the
server answers. "Works on my machine" here meant "a server was already running
every time we tested".

## 2026-09-20 — Copy/paste dead in the Mac app
**Broke:** ⌘C/⌘V (and ⌘Q/⌘W) did nothing.
**Cause:** a programmatic NSApplication has no main menu, and macOS key
equivalents only exist via menu items.
**Rule:** a minimal AppKit shell still needs App/Edit/Window menus. Smoke-test
the boring OS integrations (copy, paste, quit) on every new native shell.

## 2026-09-20 — Hooks failing with exit 127 in every session
**Broke:** SessionStart/UserPromptSubmit/Stop hook errors in codex; events
degraded.
**Cause:** hook configs (`~/.codex/hooks.json`, `~/.claude/settings.json`) had
absolute paths into a repo checkout that was later moved. A zombie app process
was also still running from the deleted path.
**Rule:** never wire user-level config to a repo checkout path; point it at an
install location that survives moves (the pipx venv). After moving/renaming a
repo, grep configs for the old path and `pgrep` for processes still running
from it.

## 2026-09-20 — "Clear terminated" left tmux panes and worktrees behind
**Broke:** 6 orphaned tmux sessions (some weeks old) after clearing terminated
sessions; orphaned test worktrees.
**Cause:** the bulk endpoint deleted DB rows directly instead of going through
the single-session teardown (stop supervisor, kill tmux, remove worktree).
**Rule:** bulk operations must call the same teardown path as the single-item
operation — never reimplement a subset. If deleting X leaves any resource of X
alive, the delete is wrong even if the API returns 200.

## 2026-09-20 — Release wheel built without the dashboard
**Broke:** a naive `python -m build` produced a wheel missing the web UI —
exactly the "dashboard isn't built" failure users hit on 0.3.4.
**Cause:** bypassed `scripts/build_package.sh` (which builds web/ and bundles
it) because the release script was blocked and steps were redone by hand.
**Rule:** when re-running a blocked script manually, execute its steps, not
your memory of them — read the script first. Verify the artifact (list the
wheel contents) before publishing.

## 2026-09-20 — `duckterm serve` wasn't a one-command experience
**Broke:** fresh installs printed a URL to copy-paste; a port collision showed
a dev-only "build the dashboard" instruction to installed users.
**Cause:** UX written from the dev-checkout perspective; error messages didn't
distinguish dev checkout from broken install.
**Rule:** every user-facing message must be written for the audience that will
actually see it. If a condition has two causes (dev vs installed), say which
one applies.

## 2026-09 (earlier) — CI red for weeks; flaky permission test
**Broke:** both CI jobs failing (import errors, Linux-only PTY EIO,
`gettempdir().parent == "/"`); a test flaked on same-millisecond timestamps.
**Cause:** bare `pytest` doesn't put the repo root on sys.path (use
`python -m pytest`); Linux PTY semantics differ from macOS; wall-clock
ordering assumptions break at millisecond resolution.
**Rule:** CI must run the same invocation devs run; reproduce Linux failures
in Docker before guessing; never assert strict ordering of wall-clock
timestamps taken in the same millisecond.

## 2026-09 (earlier) — Share viewer stuck on "disconnected, retrying"
**Broke:** relay viewer page never connected.
**Cause:** auth token passed as a WebSocket subprotocol, which browsers reject
unless the server echoes it back — and the relay wasn't validating tokens at
all.
**Rule:** pass browser-WS auth in the query string or a cookie, never as a
subprotocol you don't echo. "It connects" is not "it authenticates" — test
both the happy path and a wrong token.
