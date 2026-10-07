# Retro — lessons from real breakage

## 2026-10-06 — An explicit recovery choice needs a safe way back

An incorrect transcript attachment must be reversible without deleting its file or starting an agent. Expose Undo only for an adopted binding on a stopped session, send the current revision, and leave launch-assigned or observed identities alone. Disable Resume while Undo is unresolved. A lost response requires a fresh identity read before another action, never an automatic mutation retry. Verify the complete attach/undo/reselect workflow and compare transcript bytes before and after Undo.

## 2026-10-06 — Show missing conversation identity before Resume is needed

A launched agent could work normally while no conversation ID was recorded, hiding the loss until a later Resume. Show identity, hook configuration and readable transcript readiness as separate facts. Recovery must use an explicit owner choice with bounded project-scoped discovery, opaque handles, whole-file verification and an atomic session revision/duplicate-ownership check; never choose the newest transcript or automatically resume. Preserve a generation barrier when undoing an adopted binding so old hooks cannot restore it. Keep filesystem work off the event loop and reject discovery without a recorded absolute project directory. Test stale files, competing attachments, missing directories, expired handles, uncertain responses and real-browser recovery.

## 2026-10-06 — Distinguish missing resume inputs from unsupported adapters

A known transcript adapter with no recorded directory was labeled as lacking file-per-conversation lookup. Report the missing directory explicitly so diagnostics direct the owner toward the actual missing input. Keep malformed hook configuration private and report unknown rather than echoing it.

## 2026-10-06 — Empty launch generations must receive a fresh token

Resuming an unidentified legacy conversation passes an empty generation override.
Using setdefault preserved that empty value when assigning its first native ID,
so valid hooks from the new process were rejected. Generate a nonempty token for
both absent and empty values, while preserving an explicit switch generation.
Reproduce through Resume and verify the child's matching hook is accepted.

## 2026-10-06 — Validate identity evidence before recording a conflict

A malformed current-generation SessionStart could permanently contest an assigned conversation and block Resume. Apply the same native-ID validation to conflicting evidence as to an initial binding. Invalid hook input should be dropped without changing a valid recorded identity; valid mismatches must still remain contested.

## 2026-10-06 — Native bug reports need reviewed server readiness

A post-reboot Resume report contained only app startup events, leaving no
evidence about resumable IDs or transcript availability. The native reporter
must retrieve the server's redacted resume-readiness item, wait for that snapshot
before export, and show the same text it writes. Keep unrelated context out;
never append raw network errors. The existing diagnostics opt-out must exclude
the entire snapshot. Label local scope explicitly and retain graceful fallback
for older or unavailable servers.

## 2026-10-06 — Resume diagnostics must follow durable identity precedence

Saving an observed ID outside retained events is only useful if diagnostics also
read it. Prefer explicit native bindings, then the saved observation for the same
harness, then legacy events. Keep contested and pending bindings visible and never
probe their transcript as though the identity were usable. Verify the report after
event deletion and ensure it still exports no IDs or paths and writes no data.

## 2026-10-06 — Conversation identity must precede the process

Ordinary launches learned conversation IDs only from optional hooks, then lost
that evidence when old events were retained away. Assign Claude/Copilot UUIDs
and commit them with the session before spawn; persist observed identities too.
The event bus previously swallowed persistence errors, so launch now requires
its identity write to succeed. Preserve generation barriers and switch rollback;
never backfill a pending binding from the previous conversation. Test a real
no-hook child reading its committed ID, failed writes, retention, duplicate UUIDs
and current-generation mismatches. Reuse durable native_binding metadata rather
than introducing redundant columns and an unnecessary schema migration.

## 2026-10-06 — A resume report needs identity readiness, not just app startup
The reboot report carried five startup events but omitted whether the native
conversation ID or expected transcript was missing. Add bounded read-only
readiness checks to removable diagnostics, exporting only ID presence and file
existence. Keep SQLite on its owner thread and filesystem work off the loop.
Do not export conversation IDs, transcript contents, credentials or arbitrary
paths, and do not mistake hook configuration for successful event delivery.


## 2026-10-05 — Two terminals in one session need separate paste targets

A companion shell shares an agent's session identity but not its input stream.
Keying an asynchronous image paste only by session allowed a focus change to
send the completed path to the sibling terminal. Give each shell viewer a
separate paste target, retain the owning session for uploads, and reject a late
result after focus changes. Verify collapse/reopen against a real shell: close
the viewer without ending its process, and bind destructive confirmation to the
backend's observed process token rather than a cached busy label.

## 2026-10-05 — Cleanup must distinguish absent groups from denied signals

Shell browser checks passed but the runner failed probing the departed process
group with EPERM. Do not blanket-ignore permission errors: independently list
process groups and accept absence only after a successful, nonempty listing.
Keep denial for a live group or failed inspection visible, and avoid sending a
final signal after confirming absence. Preserve the browser runner exit status.

## 2026-10-05 — Wait for card readiness before testing a pointer action
The archive Undo browser flake was a missed second Archive click, not a missing
Undo response. Undo remounts the session card; its asynchronous restart reason
adds a grid row and can move Archive between mouse-down and mouse-up. Holding
and releasing the real response reproduced the missed click with no second POST.
Wait for the fixture's restart reason before clicking, preserving the existing
Undo/reload/expiry assertions instead of extending their timeouts.

## 2026-10-06 — New npm advisories blocked every PR's web job
CI's `npm audit --audit-level=high` began failing on unrelated PRs when
source-map-js (high, build-time denial of service) and DOMPurify (low, XSS in
IN_PLACE mode, which DuckTerm doesn't use) got advisories. `npm audit fix` would
also have moved mermaid 12.0 to 12.1, pulling chevrotain 11 to 13; update only
the flagged packages (`npm update <pkg>`) and diff the lockfile's resolved
versions before committing. KaTeX's low advisory needs a breaking mermaid
change and stays open until mermaid ships a fix.

## 2026-10-05 — The gate's temp log name broke on macOS
`mktemp /tmp/duckterm-gate.XXXXXX.log` only works where mktemp replaces X's
that aren't at the end. macOS's BSD mktemp replaces trailing X's only, so it
created the literal file `duckterm-gate.XXXXXX.log`; every later gate without
GATE_LOG then failed immediately with "File exists". Readers of the old shared
log were also seeing another session's results. Keep the X's at the end of a
mktemp template, and test temp-file names on macOS, not only in CI's Linux.
Found by oracle-main-dev.

## 2026-10-06 — Linux shell launchers can outlive their command
A tmux shell command without explicit exec left a /bin/sh launcher on Linux.
The pane PID belonged to that parent while the interactive owner shell held a
different foreground process group, so idle shells always required confirmation.
Exec the helper into the pane process; the helper already execs the owner shell.
Verify pane PID equals interactive $$ with real tmux on Linux and macOS, and
retain busy-job checks rather than increasing idle-wait timeouts.

## 2026-10-05 — Derived names cannot grant cleanup ownership

A legacy agent may already occupy a proposed sibling-shell name. Explicit shell
open/close must refuse that collision, but parent archive/delete must skip the
unowned target and finish ordinary cleanup. Test two real agents with colliding
names: deleting one must leave the other alive and discoverable.

- A new sibling-shell suffix is not ownership: older agent keys can already use it. Filter discovery by the explicit owner-shell tag, publish that tag with creation, and keep shell eligibility checks out of ordinary agent archive/delete. Regress legacy-key discovery and both cleanup paths with private real tmux sessions.

## 2026-10-05 — A surviving sibling can impersonate a departed tmux target

The session-shell integration test hung stopping an agent: tmux resolved its
missing name by prefix to the still-running `-sh` sibling. Use exact session and
pane targets for agent liveness, input, output, resize and kill operations. tmux
has different syntax for an exact session (`=name`), a pane in that session
(`=name:`), and session option lookup (literal name). Test with real tmux, including
agent exit while the sibling survives. On macOS, tcgetpgrp on another controlling
tty fails with ENOTTY; query process groups through ps and require confirmation
when inspection fails.

## 2026-10-04 — A remote Codex couldn't save October's conversations
On duckterm-dev, Codex (running as the service user) got Permission denied
creating ~/.codex/sessions/2026/10, because the year directory, and September's
before it, was owned by root with mode 0755. The repair changed only those two
directory owners (no chmod, no recursion, no restart) and verified a
create/write/fsync as the real user. Create runtime state as the service user,
and check that a new date directory can be written, instead of assuming
whoever created the first month got the ownership right. Don't conclude the
installer caused it without evidence.

## 2026-10-04 — A SQLite rowid watermark can be reused after deletion
Timeline cursors bounded inserts by MAX(rowid), but deleting the highest row
allowed a later backdated insert to reuse that rowid and enter older pages.
Retain the boundary record ID and reject continuation if that anchor changes.
The schema-free tradeoff is an explicit refresh after boundary deletion, even
when that table-wide boundary belonged to another session. Regression coverage
must combine deletion with insertion, not only append records between pages.

## Session timelines need stable ties and honest source boundaries

A timestamp-only cursor loses records when events share a millisecond. The read-only timeline uses timestamp plus stable entry ID and insertion high-water marks; regressions insert tied and backdated records between pages. Counts and source reads remain on their SQLite owner thread. Existing mutable stores are not immutable snapshots: document changes/deletions and missing producer events rather than inventing historical entries or promising constant-time scans without indexes.

## 2026-10-04 — One-shot expiry must cross its deadline
After dashboard clocks were isolated, a celebration could stay forever when its
one-shot timeout read the wall clock one millisecond before its expiry. Clamp
the expiry callback to the deadline; do not rely on an unrelated parent render
to remove it. Cover the early wall-clock boundary as well as ordinary expiry.

## 2026-10-04 — Teardown must wait for the last state writer
The v0.4.114 gate passed all browser checks but failed removing the test home.
SIGTERM and tmux kill-server initiated shutdown; they did not prove the server
or its pipe writers had stopped creating files. Wait for process exit, bound
escalation, confirm the private tmux server is gone, and await writer EOF markers
before removing state. Retried removal is a backstop, not the synchronization.
A real child that writes for 500 ms after SIGTERM fails the original teardown
and passes the corrected ordering. Count gate exit status, not passing specs.

## 2026-10-04 — Passing isolated layers hid an adopted-session restart failure
Runtime mocks, browser responses and generic-pane adoption each passed while
the real sequence (server replacement, Claude adoption, model restart, Messages)
lost runtime identity. Keep one process-boundary regression using a real tmux
pane and transcript, with a synthetic CLI recording the resume/model arguments.
Counts are not coverage: record which boundary is real and which is simulated.
Browser fixtures also used the developer HOME and a shared state filename;
create a private HOME, port, state file and tmux namespace before any worker
starts, and verify cleanup on failure and termination. Discover all browser
files in CI so new workflows cannot silently fall outside a filename whitelist.
Native UI scripts must compile the application's dependency graph: the report
runner omitted SessionTransport and navigation-policy dependencies and broke.

## 2026-10-04 — Browser teardown must own its run state
Parallel browser runs used one default state file, so helpers read another
server and teardown could target its processes. Allocate a unique state path
per invocation, inherit it in workers, create it exclusively, and verify the
run identity before reads or cleanup. A custom port alone does not isolate
a run. Keep explicit overrides, but reject existing or foreign state.

## 2026-10-04 — Refresh report flows against current runtime boundaries

A reviewed report branch can become incompatible while waiting for release.
Keep HistoryStore reads on its owning event-loop thread when integrating the
thread guard; leave ZIP/file work on workers. Detect the native report action
explicitly so older Mac builds keep the browser form instead of swallowing it.
The Settings and Help entry points should reuse the same native editor.

## 2026-10-04 — Resume proof must not hide a safe harness-switch path

Restart applied exact-conversation checks before offering any action. A missing
transcript therefore hid the option to start a different harness with a summary.
Report options per path: exact resume retains its identity guard; a seeded switch
requires live-terminal, folder, transfer, draft and real parent-turn checks, then
checkpoints before stopping. Persist a new identity boundary before launch, retain
the previous conversation for recovery, and reject late hooks from the old harness.
A daemon target remains unavailable with a reason until its independent native-ID
resolver ships. Regression coverage must prove missing resume identity still offers
a switch, model catalogs are harness-specific, failed launches preserve old resume,
and input during checkpoint work prevents stop. A dead replacement process is not
necessarily a drained supervisor: settle output/EOF before restoring the old
runtime, including partial startup failure and cancellation. Drain the captured
supervisor instance, not whichever process later occupies its key. Rotate hook generations on later
exact resumes too, so delayed same-conversation hooks cannot control a new launch.
No schema change was needed.

## Output failure paths must retain evidence and release resources

QA found that an invalid writer marker ended capture without an error field and that non-ENOENT spawn errors leaked PTY descriptors. Tail failures now carry output_error on SessionEnd, and every spawn exception closes unowned descriptors, including cancellation. Deterministic negative cases verify error reporting and closed descriptors rather than relying on a happy-path output test.

## 2026-10-04 — Agent exit does not prove output capture finished
On macOS, keep the parent PTY slave open until final reads complete; closing
the last slave can discard bytes before a delayed reader ever sees them.
A short-lived agent can exit before tmux piping attaches, or its output writer
can flush after the tailer reads EOF. Attach capture before releasing startup;
wait for the writer to acknowledge EOF and perform a final drain before sending
SessionEnd. Preserve a bounded, reported failure if the writer never confirms
completion. Test the final-probe race deterministically and delayed PTY reads;
do not hide lost output with sleeps or retries in the regression.

## 2026-10-04 — The Mac app said macOS 13 but only launched on 15
A friend on macOS 14 couldn't open DuckTerm. Info.plist declared 13.0, but
build.sh called swiftc without -target, so the binary inherited the build
machine's OS (15) as its minimum; Package.swift's .macOS(.v13) is never used by
that build. Set the target explicitly from the same value Info.plist uses, and
fail the build when `otool -l` reports a different minos. Check what the binary
says, not what the plist says.

## 2026-10-04 — Change model left a session stopped; Resume lost its conversation
**Broke:** the owner changed the model on `kaho`. Restart verified the
conversation, stopped the agent, then refused to relaunch ("Cannot verify the
exact conversation to restart"), leaving the session stopped. Resume then
started a fresh, notes-seeded conversation on the wrong model; the original
conversation was intact on disk the whole time.
**Cause:** `Orchestrator.reconcile()` re-adopted every pane that survived a
server restart with `GenericRuntime("true")`. When the adopted agent exited,
its SessionEnd carried `runtime: generic`, and the session upsert
(`runtime = COALESCE(?, runtime)`) overwrote `claude-code`. Restart's post-stop
resume and the Messages tab both choose their transcript reader from that
column, so both failed for any session that had outlived a server restart.
**Fix:** adopt each pane with its own harness (`runtime_for` from the row's
runtime, or inferred from its command). Regression test asserts an adopted
pane's SessionEnd keeps the harness and leaves the row unchanged.
**Lesson:** an adapter chosen for convenience in one code path still writes
identity into shared state. Anything that emits events on a session's behalf
must carry that session's real harness, not a placeholder.

## 2026-10-04 — Filter inbox work before paginating history

A filter over the newest loaded messages can report no outstanding work while
older requests remain unanswered. Apply reply-state predicates before the page
limit, return counts across the scope, and reset the cursor when views change.
Read priority messages still require a reply; read state is not completion or
terminal delivery. Verify with more than one page of both history and open work.

## 2026-10-04 — Isolate every clock that can redraw the terminal
A one-second clock in Dashboard redrew terminal and connector components with
30 sessions even when nothing changed. Moving only that clock would leave the
voice scheduler and unchanged archive poll responses triggering the same work.
Keep time subscriptions below the dashboard, retain identical poll snapshots,
and give archive countdowns their own clock. Verify render counts while time
advances, including the 30-second idle and 90-second voice grace. Pause History
polling when hidden and discard late responses after changing sessions.

## 2026-10-04 — Hidden filters must not hide active filtering
Filter controls competed with the session list and their compact layout crowded
labels against chips. Keep the panel closed by default and remember its visibility
per viewer, independently of the selected filters. When controls close, retain an
active-count badge and a summary with Clear filters. Use distinct chip rows so
label and group spacing stay uniform across densities, and show Local/Remote even
when the current remote count is zero. Verify collapse, reload and keyboard access
with filtering active; hiding controls must not reset the session selection.

## 2026-10-04 — Separate connection startup from transaction races
A pin-limit test rendezvoused while another worker was still constructing its
HistoryStore. Startup performs repair and retention writes, so a short barrier
could time out before the operation under test. Prepare connections serially on
their dedicated owner threads, then race only the pin transactions; preserve the
one-winner and three-pin assertions. Constructor failures must surface directly.

## 2026-10-04 — Enforce SQLite ownership across indirect worker calls
Messages and progress helpers dispatched to workers still queried the shared
HistoryStore connection, even after the connector caller was moved inline.
Checking only direct `to_thread(history.method)` calls misses these paths.
Copy runtime, directory and native conversation ID before dispatching file work,
and let SQLite reject cross-thread access. Test actual async routes as well as
the guard. A partial connector-use index keeps unrelated events out of its scan;
it does not make usage aggregation constant-time.

## 2026-10-04 — Stable asset URLs must never be immutable
The approved yellow duck stayed green in the owner's Mac app because the server
gave every non-HTML file a one-year immutable cache lifetime, including the
unhashed `/favicon.svg`. A fresh browser and checking the served file missed the
persistent WKWebView cache. Cache only the content-hashed dashboard assets as
immutable; stable URLs must revalidate. Bundle the header and favicon from one
SVG source so an update changes their URLs and bypasses already-cached icons.
Verify the actual app window after release, not just a fresh browser profile.

## 2026-10-03 — A convention nothing checks is a trap for the next caller
**Broke:** `GET /connectors` logged an `IndexError` on a zero-column row.
Shipped in #149.
**Cause:** `HistoryStore` opens its sqlite connection with
`check_same_thread=False` and no lock, so its safety rested entirely on every
caller staying on the serving thread. Nothing stated or enforced that. I
wrapped one query in `asyncio.to_thread` to keep the panel responsive — the
only one of 59 `to_thread` calls in server.py to touch the database — and it
raced the 17 write paths on that same connection. Two readers plus two
writers on one connection produce thousands of errors in seconds, including
corrupted writes, so the panel's `IndexError` was the mildest symptom.
**Rule:** when a shared resource is safe only by convention, encode the
convention as a test the next person will trip over, not a comment they will
not read. The guard here records which thread the query ran on and fails if
it is not the serving thread; reverting the fix turns it red. Also: diagnose
before accepting a plausible explanation — this was first attributed to a
connector with no usage rows, which turned out to be handled correctly
(zero matching rows returns an empty mapping and nothing is indexed), and
fixing that would have left the real race in place.

## 2026-10-02 — Filters must agree with the rows they hide
Status shortcuts use the same effective state as session rows, including Stop
settling. Keep folder expansion outside the filtered tree, preserve session
selection, exclude archived rows at the filter boundary, and read preferences
back after saving. Brand text belongs in one flex child so icon spacing does
not split the wordmark; regenerate normal and Test icons together.

## 2026-10-01 — A shared .venv gates the wrong worktree
**Broke:** a gate in a fresh worktree failed three tests whose fix was
present in that worktree's source. An earlier gate of a specific SHA reported
PASSED without having run that SHA's code at all.
**Cause:** the worktree's `.venv` was a symlink to another worktree's venv, so
the editable install still resolved `duckterm` to the *other* checkout.
`python -c "import duckterm.server as s; print(s.__file__)"` pointed at a path
the gate was not testing. Symlinking is tempting because `pip install -e`
takes a minute per worktree.
**Rule:** every worktree gets its own `python -m venv .venv` and editable
install — never a symlink to another checkout's. When reporting a gate result
for a named SHA, confirm the interpreter resolves the package inside that
worktree before trusting the verdict; a green gate on the wrong source is
worse than a red one, because it is reported as evidence.

## 2026-10-02 — Schema numbers do not prove feature presence
Folder Tasks shipped schema v10 without the unreleased v9 fork-merge tables.
Integrate both lifecycles, create missing merge tables and columns by existence,
and advance to v11 so older code refuses to reopen merged children. Test both
v10 without merge tables and v9 with a delivered, final child; preserve Tasks
and checkpoints across migration and repeated startup.

## 2026-10-01 — Saving a merge is not delivering it
A reviewed fork summary must survive retries without duplicating the parent's note.
Persist the close-child choice before enqueueing through the shared priority broker;
create the parent checkpoint only from confirmed delivery, with an idempotent ID and
startup recovery for the delivery/checkpoint crash gap. Keep merged children final
even when a delayed SessionStart arrives. A failed close remains visibly pending and
can be finished from History using the same saved request.

## 2026-10-01 — Measure a performance fix on the owner's machine, not just a benchmark

**What happened:** #192 bounded terminal polling, with reads drained at most 64 KiB per tick, tmux liveness probed at most once a second, and screen scans batched. main-dev's synthetic benchmark used harmless `/usr/bin/true` liveness probes instead of real tmux, and predicted roughly an 81% cut (34.88% to 6.69%).
**Measured on the owner's live server** (ps CPU time of the server process, six 10-second windows, 28 panes, compared at similar uptime): v0.4.99 **72.0%** to v0.4.100 **14.9%**, about 79%. Product independently saw 11-13% afterwards.
**Rule:** take a live baseline BEFORE installing a performance fix, measure after at comparable uptime and pane count, and record both in the release PR. A synthetic number is a prediction, not a result; this one happened to hold, but the before-and-after on real panes is what makes the claim.

## 2026-10-01 — A "scratch" Codex test changed the owner's real Codex
**Broke:** checking Codex's question tool, the Oracle main-dev session ran a
fresh `codex` in its own tmux socket. It used the owner's real install and
`~/.codex`. Keystrokes meant for the prompt landed on Codex's startup update
prompt and chose "Update now", so the owner's Codex went from 0.155.1 to
0.159.3. 0.159.3 starts a shared app-server daemon per `CODEX_HOME`, which
outlives the session that started it. This one held the probe's environment
(`DUCKTERM_URL` set to a dead port, the probe's session key), so the hooks
of any new Codex session would have gone nowhere. The daemon was killed at
09:50, and no owner agent was attached to it.
**Cause:** "isolated" covered the DuckTerm side (own home, tmux socket, port)
but not the agent's own home. An interactive agent start can update itself
and leave processes behind. Keys were typed without first checking which
screen was showing.
**Rule:** real-agent checks run with a scratch agent home (`CODEX_HOME`,
`COPILOT_HOME`, with update checks off). Never type into a fresh agent before
reading its screen. Afterwards, list and stop every process the check
started, daemons included.

## 2026-10-01 — Assignment must be one transaction
A task without its inbox message is invisible work; a message without its task is
an invisible assignment. Create the explicit assignment inside the inbox broker's
transaction, bind the assignment choice into retry identity, and retain the same
task ID through handoffs. Archive retired Work rows to a private, durable JSON file
before dropping their tables; a failed archive must leave the old tables intact.

## 2026-09-30 — A remote report bundle is bytes, not UTF-8 text
**Broke:** The generic Mac request bridge rejected report routes, limited JSON
bodies to 1 MiB, and decoded every response as UTF-8. That would reject valid
attachments and corrupt ZIP downloads from a remote server.
**Fix:** Report-specific method/path checks and bounded body/envelope limits;
base64 binary replies for report bundles, decoded into bytes on the client.
**Check:** Native tests exercise the full attachment budget, unchanged unrelated
limits, invalid operations, and binary response equality. Browser tests keep the
reviewed report and selected files unchanged through draft preparation/download.

## 2026-09-30 — A mail draft is not a delivered bug report

Keep the preview's UTF-8 bytes unchanged through report Markdown, mailto and MIME export. Preparing a draft is not sending mail; attachments cannot travel in mailto, and long URL bodies need a complete file fallback rather than truncation. Remote users need an authenticated bundle download, not only a server path. Collect canonical event metadata without reading hook payloads or terminal content, and test attachment limits and private-file reads.

## 2026-09-30 — Artifact protection belongs in the store

Keep must reject removal on the server, survive re-registration, and preserve the owner’s category. Removing an unkept saved copy should retain metadata in a separate table, not leave an empty downloadable file. Folder counts must use exact subtree membership and the same durable mail and transcript accounting as Analytics; test both live and retired mail. Automatic retention remains a separate owner decision.

## 2026-09-30 — Saved widgets must stop reading when removed

A hidden tile still polling is not a removed widget. Give each built-in widget only its declared streams, detach its readers on unmount, and stop the shared insights timer after the last subscriber leaves. Missing data must say Unavailable instead of reporting zero. Persist layouts on the server with revision checks and recoverable folder rename/delete intents so reloads and crashes do not lose the owner’s arrangement.

## 2026-10-01 — Cached empty lists lost transcript availability
**Broke:** The Messages snapshot retained only the message list, so unavailable reasons vanished on remount and status-only changes shared the same empty-list signature.
**Fix:** Retain and compare transcript status and reason atomically with the list, including cache size accounting; cover unavailable, empty, recovered, hidden and remounted states.
**Lesson:** Cache the full user-visible response contract, not only its main collection.

## 2026-09-30 — The verifier repeated the bug it was built to catch
**Broke:** main-qa returned PR #149. `verify` reported ok for a connector
listing `{tools: []}` and for one listing `{tools: [{}]}` — an empty set and
a nameless object both read as "Verified".
**Cause:** the validation checked that a reply arrived and was shaped like a
list of objects, which is the cheap question. The real question is whether an
agent can call anything, and an agent calls a tool by name. This is the same
defect the feature exists to fix, one layer up: the panel used to assert "a
config entry exists" while appearing to assert "this works", and the verifier
then asserted "a reply parsed" while appearing to assert the same thing.
Writing the check does not exempt it from the standard it enforces.
**Rule:** when adding a check, state the claim it licenses in the UI's own
words and test the weakest input that should fail it — here, an empty list
and `{}`. Prefer failing a whole listing over skipping bad entries: counting
2 of 3 overstates what the agent can reach. And a judgement call made alone
is worth re-examining when a reviewer disagrees — the earlier "an honestly
empty server is not broken" was defensible in isolation and wrong against the
sentence the panel actually prints.

## 2026-09-30 — A written config entry is not a working integration
**Broke:** the connectors panel reported `codex: ✓` on machines that had
never had Codex installed, and `enable()` wrote both harness configs
unconditionally with no way to choose.
**Cause:** the checkmark reflected "we wrote a line into a file", which is
the easiest thing to know and not the thing anyone wants to know. Nothing
checked whether the agent CLI existed, and the credential (machine-wide) was
welded to the registration (per harness) behind one switch.
**Rule:** report the state the user cares about, not the state that is cheap
to compute — name the agents a connector reaches and say which are missing,
rather than printing a tick per config entry. When one action does two things
at different scopes, let the user address them separately. A registration for
an absent harness is still legitimate (it applies when that harness arrives),
so label it rather than blocking it; the defect was the false claim, not the
write. Deselecting must also withdraw the existing entry — otherwise the
harness keeps serving a connector the panel no longer lists.

## 2026-10-01 — Codex's questions to the owner never reached Oracle
**Broke:** the owner was told to approve ui-dev's work in Oracle, and nothing
was there. Codex asks the owner through `request_user_input_async`, and
Oracle only made choice notes for Claude's `AskUserQuestion`. Of 69 Codex
questions, 14 were never answered. For 8 of those, the next prompt submitted
was Oracle's own inbox reminder, which silently discards a queued Codex
question. Copilot's `ask_user` arrived without its question, because the
hook read `toolInput` and Copilot sends `toolArgs`.
**Cause:** the choice note was keyed to one harness's tool name, and the
empty-prompt check couldn't see Codex's queued question above an empty box.
**Rule:** how an agent asks the owner is a declared harness capability
(`Harness.owner_prompt`), checked against a real event for each harness. A
harness that leaves it undeclared can't ask the owner through Oracle. Before
Oracle types into an agent, it must know whether typing destroys something
the owner hasn't seen.

## Terminal display cadence must not drive tmux process creation
The pane tail could drain continuously without yielding, scanned hook-capable TUI repaints per line, and launched a tmux liveness subprocess on every empty25–200ms poll. Bound each tick to64KiB before a25ms yield, check liveness at most once/second independently of display latency, and batch hook-capable screen fallback every250ms. Preserve raw bytes and generic per-line protocol events; flush pending PTY evidence even when output goes quiet. A28-session synthetic benchmark reduced process CPU from34.9% to6.7% with identical840,000 delivered bytes; this is not an installed-server CPU claim. Tests cover fairness, probe cadence, rotation, quiet prompts and replay.

## Confirmed parent removal must survive frontend replay
Removing a parent row left child controls gated by a stale parentKey; null-coalescing then restored the original edge from old fork events. Represent a confirmed absent parent as null, keep database lineage authoritative during seed, and clear direct links immediately on deletion. Check both seed/replay orders, late events, remote host isolation, and the real dashboard delete/reload flow while preserving descendants and historical provenance.

## Deleting a parent must remove active child links, not child sessions
Parent deletion left children pointing at a removed row. Clear direct child parent_session_key values in the deletion transaction while retaining their recorded fork events. Reject links to tombstoned parents in late supervisor events before live fan-out, and repair old tombstoned-parent edges on reopen without guessing about unknown parents. Tests verify descendants remain attached to their surviving parent and a real child PTY process stays responsive after its parent is deleted. Dashboard local-state cleanup is a separate UI integration requirement.

## 2026-10-01 — A zero-size layout callback discarded terminal resize
**Broke:** Pane transitions could call the terminal resize observer before usable dimensions existed; returning silently left stale terminal columns.
**Fix:** Retain the pending fit on the next animation frame until measurable, cancel it when hidden or disposed, and keep attach scrolling separate from ordinary reflow.
**Lesson:** A temporary layout failure needs a retry, not an assumption that the observer will fire again.

## 2026-10-01 — Browser permission was mistaken for notification preference
**Broke:** Turning notifications off did not survive reload; denied permission gave no explanation, and initial waiting sessions produced a burst.
**Fix:** Persist preference independently, respect current permission, show actionable feedback in the existing help slot, and notify only observed non-waiting to waiting transitions after host loading and enablement. State explicitly that the independent native Mac notifier is separate.
**Lesson:** Permission is a capability, not a saved preference; initial snapshots are not live transitions.

## 2026-10-01 — An update control is not an updater
Keep Install disabled until the detached installer, snapshot, restart verification
and rollback contract is implemented. Read release information only when Settings
opens or the owner explicitly checks again. A failed check clears the latest
version, and a successful operation must not say Updated until the installed
version matches the verified target.

## 2026-09-30 — Priority status said "delivered" before anything was
**Broke:** in review of PR #173, a priority broadcast whose Oracle reminder
got stuck in the prompt, or failed to paste, already showed "delivered". A
plain broadcast retried with its old `request_key` would have hit a 409.
**Cause:** rendering the pinned block also marked it delivered, before the
paste was tried. Adding priority to the idempotency hash changed the hash of
every plain broadcast too.
**Rule:** record delivery from the result of the delivery, never from
building the text. When adding a field to a stored hash, keep the old hash
for the old shape.

## Fork children need independent identity and inherited test scope
Conversation forks derived their child key from the parent's native ID, so a second fork reused the first child's supervisor key. Generate a fresh DuckTerm key for every fork while retaining the native ID only in the resume command. Both conversation/worktree paths must inherit a test parent or explicit test request, including terminal SessionStart rows; test repeated forks and both launch modes without live inference.

## 2026-09-30 — A raised hand is the owner's to lower
**Broke:** a session that asked for the owner dropped its raised hand on its
next event, whether or not the owner had seen it, and the session list let a
screen reading override a hook-driven "waiting". Ducks also looked busy for 5
minutes after an agent finished.
**Cause:** "needs you" was modelled as a session state, which the agent's
own events overwrite. The screen check also overrode hooks for every
harness except Claude Code.
**Rule:** keep attention apart from state. `attention_since` is set when a
session starts waiting and is cleared only by the owner: opening it, or
answering its note or approval. The screen may lift only a wait the screen
itself established (auto-reviewing Codex); hooks win otherwise. The duck
settle is 30 s (owner decision); a Stop starts it, so pauses inside a turn
can't flicker a duck idle.

Also, from the real-agent check: a second DuckTerm server on the machine
needs `DUCKTERM_TMUX_SOCKET` (or it adopts the owner's agents), a port
checked to be free (another session's browser tests had hit a fixed port),
and `DUCKTERM_NO_BROWSER=1`. `duckterm serve` opens the dashboard in the
owner's browser, and that tab then acts on the test; here it marked the test
session attended.

## E2E readiness must identify the spawned server
A busy test port let global setup accept another QA server's public sessions response. Readiness now matches the dashboard token against the private test home, checks child exit/errors, and fails on deadline; failed setup reaps only its child and removes its home. Regressions cover a foreign listener, early child exit, timeout and matching identity.

## 2026-09-30 — A shared artifact title must resolve through session credentials

A peer review stalled because only the owner dashboard could read another
session's saved artifact. Add scoped metadata and snapshot reads to the session
API, checking both current sharing roots and folder membership. Filter before
pagination; recheck on download; preserve access to stopped producers without
granting writes. Download saved bytes rather than reopening a peer's source path,
verify their digest, and create a new private file without overwriting user data.

## 2026-09-30 — Polling tests must update the server fixture after a mutation
**Broke:** The restart test lost its pending message when a two-second refresh
ran under full-gate load. The POST mock returned queued, but the GET mock kept
returning the old ready state.
**Fix:** The POST fixture also updates the subsequent GET response, matching the
server’s durable restart behavior. The visible pending and cancel assertions stay.

## 2026-09-30 — Folder messages must outlive the inbox cleanup window
**Broke:** Folder chat answered questions but could not address a session. Reusing
inbox records without saving their replies would erase conversation content
when the broker retires closed mail after seven days.
**Fix:** Explicit recipients use the existing owner inbox path with folder scope
checks and idempotent sends. Replies are copied into bounded folder history
before retirement, including replies the owner has not opened yet.
**Check:** Regression coverage sends, retries, answers, retires the inbox row,
restarts the store, and verifies the complete reply remains in folder chat.

## 2026-09-30 — Missing transcripts looked like empty conversations
**Broke:** Messages ignored the API transcript status and said no reply existed when the conversation identity or local file was unavailable.
**Fix:** Render the recorded unavailable reason and distinguish missing identity, missing local transcript, and a genuinely empty conversation; clear status on session changes and successful recovery.
**Lesson:** A backend correctness fix needs its unavailable-state contract rendered at the user-facing boundary. Test both transitions and recovery.

## Conversation identity and launch names must survive reopening
A newest-transcript fallback showed a peer conversation when the recorded native ID or its local file was missing; Claude resume used the same guess. Require the recorded ID for Claude/Codex Messages and Claude resume, and report missing identity/file explicitly. Separately, launch names appeared in SSE but the session-row INSERT discarded them, so reload fell back to the folder label. Persist names and recover missing historical names only from explicit saved launch events, preserving owner renames. Regressions cover two conversations sharing a directory, missing local transcripts, unsafe resume refusal, and database reopen.

## Measure Messages refreshes with many small records, not only large text blocks
Deep-copying cache results fixed mutation leakage but made a 20,000-record warm read expensive. Keep detached object reads for callers that need them; cache immutable, fully serialized Messages HTTP bytes and session-specific keys for polling. Invalidate on transcript changes or native-ID scope changes. A 20,000-record timing regression and no-read/no-copy/no-serialization assertions cover the actual HTTP response path alongside the unchanged nested-mutation regressions.

## Cached messages must detach nested response data
Copying only each message dict protected added message keys but shared nested blocks and tool-input dictionaries. QA showed a caller mutation leaked into later reads. Deep-copy returned records on both cold and warm paths; regressions mutate nested tool inputs and block lists for Claude/Codex, with and without a final newline, then verify append behavior.

## Messages refresh must reuse transcript parsing across runtime adapters
Every Messages request constructs a fresh runtime adapter, so an adapter-local cache would still reread the entire transcript. Claude and Codex now share bounded per-runtime JSONL caches using TokenLedger's complete-line offset pattern. Unchanged files are stat-only; changed files verify the committed prefix before parsing appended records. Size growth does not prove an append: the first gate caught a growing rewrite keeping a stale pin target. Partial trailing records are retried, and replacement/truncation/rewrites invalidate cached state. Keep parser IDs and per-session message keys stable; test fresh adapters and large files, not only calls on one adapter. Full-response serialization and frontend polling are separate follow-ups.

## 2026-09-30 — Messages must not start empty after every tab switch

Remounting a transcript view discarded its last good reply, and fixed-interval refreshes could pile up behind a slow full-transcript read. Retain a bounded memory snapshot under the host-qualified session URL, reuse unchanged arrays, and schedule each refresh after the prior request settles. Pause both transcript and comment reads while the document or pane is hidden; resume immediately on return. Verify delayed tab returns, covered Oracle panes, host isolation and pre-save comment races. A failed refresh should preserve content with an error, while denied/deleted content is cleared.

## 2026-09-30 — The slop check passed in worktrees without reading a file
**Broke:** PR #161 failed CI on an existence-only test assert, while the same
commit's local gate printed "slop-check: clean".
**Cause:** `scripts/slop_check.py` skipped any path with a `.duckterm` part,
matching against the absolute path. Every DuckTerm-managed worktree lives
under `~/.duckterm/worktrees/`, so the check skipped every file there and
passed.
**Rule:** match skip rules against paths relative to the repo root. A check
that finds nothing should be suspected until it has been seen to catch
something; `tests/unit/test_slop_check_paths.py` now proves it reads a
worktree.

## 2026-09-29 — A neural voice that drops words it doesn't know
**Broke:** in the first Kokoro trial without the GPL espeak fallback,
"main-dev" was spoken as "main", and "duckterm", "qa" and "utsava.xyz"
vanished from the audio with no error.
**Cause:** misaki, Kokoro's English front end, drops out-of-dictionary words
unless espeak is installed, and session names are mostly such words.
**Rule:** anything spoken passes through `voice/names.py`, which keeps every
word (known, pronounced, split into known halves, or spelled). The tests use
the observed failures verbatim.

Also: the first natural-voice build was 785 MB, mostly Python runtime (MLX,
transformers), not the model. The owner turned it down. The same weights
through ONNX Runtime are 303 MB and run on Intel too. Measure what the bulk
actually is before quoting a size to the owner.

Also: a test server given its own `DUCKTERM_HOME` still used the live
`duckterm` tmux socket and could have adopted the owner's agents. Set
`DUCKTERM_TMUX_SOCKET` as well for any second server on the machine.

## Settings contrast during theme changes (2026-09-30)

The Settings header inherited a background fade while its text switched themes immediately. Both settled themes were readable, but a system appearance change briefly put the new text on the old fill. Keep this control's foreground and background changes synchronous; check the transition itself, not just settled light/dark screenshots.

## 2026-09-29 — A folder view should not duplicate its sidebar

The first folder preview repeated the full session list already visible in the tree. Keep the approved surface to Chat and Artifacts, with name selection independent from chevron expansion. Preserve mounted terminals while browsing folders, use each artifact's producing session for the existing viewer, and keep folder chat history isolated. Rename every descendant conversation, recover JSON changes across DB commits, and reject answers that finish after the folder or its membership changes. Replay a pending operation at server startup before a deleted folder name can be recreated; lazy recovery can otherwise attach old history to the new folder. Verify these behaviors through the actual authenticated routes and browser flow.

## 2026-09-29 — Preserve actionable server errors on reads

Model discovery returned useful missing-CLI/sign-in guidance, but the shared GET helper replaced it with “503 Service Unavailable.” Parse string error messages for failed reads just as for writes; retain status fallback for malformed or non-JSON responses. Exercise the actual API wrapper and a failed catalog request followed by Retry, not only a mocked Error thrown into a component.

## 2026-09-29 — Model changes need choices from the installed harness

Removing ellipses did not make Change model a dropdown. Open model choices directly from that action and confirm the restart only after selection. Discover exact IDs from the installed CLI without starting a conversation; resolve aliases, preserve context suffixes, bound and reap catalog subprocesses, and expose retry on failure. Keep current models available even when absent from a new catalog, and preserve all restart/draft safety gates.

## 2026-09-29 — Light terminals need explicit selection colors

The default translucent white xterm selection disappeared on the Paper background. Set both foreground and active/inactive selection backgrounds for each light palette, and verify actual selected text after focus moves away. Improve neutral borders and secondary labels without tinting the original white/gray palette blue. Keep dark palettes unchanged.

## 2026-09-29 — The dashboard derives state too; fix both folds
**Broke:** v0.4.85 stopped the server from marking Codex "waiting" on every
permission request, but the dashboard's badge still flipped to waiting on
each one. The server's own stale-badge cleanup also showed as waiting there.
**Cause:** the dashboard folds live events with its own copy of the state
rules (web/src/sessions.ts). It ignored the new `auto_reviewed` tag and read
every Notification as waiting, while the server reads idle notices as idle.
**Rule:** a state rule change lands in persistence/history.py and
web/src/sessions.ts together, with a test on each side.

## 2026-09-29 — Assert terminal input bytes, not competing echo order

Canonical terminal echo and cat output can interleave: AAA + LF + BBB rendered as AAABBAAAB while all input bytes were correct. A batching change exposed this false gate failure. Use a raw-PTY child to record exact received bytes; this also distinguishes LF from CR, which canonical input maps together. Preserve the original failure, verify the replacement rejects a CR mutation, and clean up its test session.

## 2026-09-29 — Feedback bypassed queued terminal input

Artifact and message feedback wrote directly from a worker thread while keyboard bytes waited in a separate FIFO. Under backlog, feedback split an unfinished draft. Route feedback through the existing FIFO and await its actual delivery result; cancellation and write errors must release waiting requests. A blocked-writer regression reproduces the old overtaking without relying on CPU load. A separate per-character drain made a backlog require two tmux subprocesses per character. Coalesce already queued bytes in bounded batches with no added delay; preserve the exact byte stream and all browser assertions. Browser timeouts alone do not establish where latency occurs.

## Stacked context tabs must not resize the terminal — 2026-09-29

At the 1100px breakpoint the three panes become automatic grid rows. Different tab contents then redistributed height and reflowed the terminal. Give stacked rows explicit fractional allocations, retaining the collapsed 40px controls. Compare terminal geometry after both tab directions at the breakpoint and below it, as well as on desktop.

## 2026-09-29 — Essential controls cannot depend on leftover height

The expanded Session card left Connectors only 13–24 pixels of the right panel.
Give Session and Connectors separate, persistent views with their own scrolling
area. Check populated cards at real window heights, panel reopening and host
changes; presence in the DOM alone does not prove controls are reachable.

## Restart action labels — 2026-09-29

Owner found trailing ellipses on Restart and Change model confusing. Use the requested plain action labels; dialog behavior stays explicit in the dialog itself. Updated existing Restart UI tests and browser locators to use the visible labels.

## 2026-09-29 — Prove restart race ordering and report its durable outcome

A CI restart regression failed with zero launch calls while an unrelated
progress worker also crashed against an incomplete terminal fixture. The log
did not include the saved restart status, so it could not distinguish a lost
queue from a rejected preflight. Isolate digest/relay workers in lifecycle rigs,
hold the version probe with explicit events, exercise Stop both before and after
the old attempt exits, and drain replacement tasks with a deadline. Report the
durable state and last hook before asserting call counts. Removing the retry
handoff must fail the test; repeated green runs alone do not explain a CI failure.

## Archive Undo must precede teardown — 2026-09-29

Archiving stops the PTY and drops pending approvals, so reversing only the lifecycle flag cannot restore the session. Persist the eight-second grace period before acknowledging Archive, leave all session state untouched until expiry, and durably claim the operation before stopping anything. Recover pending and committing operations after app restart. Test full metadata/enrollment equality on Undo, expiry, stale cancellation, owner authentication, competing lifecycle actions and viewer reload.

## 2026-09-29 — Visible session actions need no overflow menu

Moving actions out of the sidebar but hiding them again behind More retained
the discoverability problem. Show applicable actions directly at real panel
width, separate Delete below, and preserve existing lifecycle gates and
confirmation behavior. Verify actual code before claiming an action confirms.

## 2026-09-29 — Buffered reads can starve an already-ready consumer

The independent tmux reader could consume a buffered burst without yielding:
readline returned immediately and put_nowait filled the bounded queue before
the viewer task ran. A healthy viewer was reaped as stalled. Yield after each
queued output chunk without adding a timed delay. Test a prebuffered burst
larger than the queue with a ready consumer; retain separate real stalled-view
cleanup tests and exact no-missing/no-duplicate output assertions.

## 2026-09-28 — Rebased lifecycle controls must preserve off-loop tmux checks

Restart initially brought synchronous liveness properties back onto the server
loop after main moved tmux operations off-loop. A probe can depend on that same
loop draining output. Run liveness probes in a worker, keep database access on
the loop, recheck supervisor identity after awaits, and inspect drafts after the
last blocking probe. Cover status and execution with a probe that requires loop
progress rather than relying only on fast fake booleans.

## 2026-09-28 — Restart needs positive turn-end evidence and a second draft check

A terminal can look idle during a tool call, and a user can type after queuing
an action. Persist the restart request with its exact native conversation ID,
release it only on the matching parent Stop hook, and recheck input immediately
before stopping. Order hook events by insertion, not millisecond timestamps.
Keep pending/failure state visible even when the session becomes stopped, and
never present a fresh installed-binary probe as the old running CLI version.
If a model change fails to launch, restore the previous preference so Resume
does not repeatedly launch the rejected model.

## 2026-09-28 — Never wait for tmux on the loop that drains its output

The terminal control stream exposed synchronous tmux calls on the asyncio loop.
A liveness probe could block that loop while tmux waited for its output client
to drain, freezing the dashboard and hooks too. Move blocking tmux operations
off-loop, including property reads, screen capture, resize, transfer and lifecycle
paths. Keep database and state mutations on-loop. A slow liveness response is
not a dead session: do not replace it with a timeout returning False. Regressions
make the fake tmux response depend on loop progress and verify the tail remains
alive until tmux actually reports exit. Approval keystrokes use the existing
ordered input queue rather than synchronously calling tmux from callbacks.

Screen capture -C doubles literal backslashes, while live output octal-escapes
them. Assuming one encoding rejected real TUI captures. Decode both forms,
preserve unknown escapes, and test OSC8, colours, Unicode, invalid UTF-8 and
literal backslashes through real tmux. Drain each viewer independently into a
bounded queue; overflow closes its client even if the browser never reads again.
During cleanup, drain subprocess pipes too: wait() can hang on a full pipe.

## 2026-09-28 — Terminal snapshots and live bytes need the same ordering source

A tmux capture could include bytes still buffered before the pane log, then a
new viewer replayed those bytes again as the file reader caught up. File offsets
and sleeps cannot identify that boundary. Use one ordered control stream for
each viewer's capture and live output; keep logging separate. Test with a frozen
file reader, mid-output attachments, and disconnected or paused viewers. Preserve
the cursor's blank row so post-snapshot output cannot overwrite the previous line.
Test input with viewers attached on the bundled version too: tmux 3.7 selects
read-only control clients for send-keys and rejects otherwise valid input. Keep
the viewer's private command channel output-only without that client flag.

## 2026-09-28 — A request is not a wait
**Broke:** Codex agents showed "waiting" while running commands, and Oracle
had no note for them. The owner saw a badge and nothing to answer.
**Cause:** every PermissionRequest set the session to waiting. Codex's own
reviewer approves nearly all of them, over a thousand in three days.
**Rule:** for an auto-reviewing harness, only the prompt on screen means
waiting. When a request is stuck on a screen Oracle can't read, show waiting
and save the screen, so the missing prompt shapes come from real data.

## 2026-09-29 — Live test agents must not expire during setup

The fleet digest fixture exited after five seconds while readiness polling alone
could use four. CI then correctly reported no live agents and failed the digest
assertions. Keep fixture processes alive until explicit finally cleanup, mark
them test:true, and send readiness output after pipe attachment. Verify with a
request delayed beyond the old lifetime; increasing sleeps only moves the race.

## 2026-09-28 — A green gate on synthetic panes shipped a server hang (v0.4.83)
**Broke:** v0.4.83's new ordered terminal replay (#130) raised `ValueError:
invalid tmux control escape` on the owner's real agent panes. The dashboard
stopped responding, and its `tmux -C attach-session … pause-after` control
clients stopped being read, which wedged `tmux ls`. Rolled back to v0.4.82 (all
25 panes preserved), withdrew the release, and reverted #130 and #127 on main.
**Cause:** the control-mode escape decoder was strict (it raised on input it
didn't expect), and a stream error left tmux control clients attached with no
reader. Every test and QA run used synthetic panes; real Claude/Codex TUIs emit
output those fixtures never produced. The post-install check was a single
immediate HTTP 200, before any terminal stream had attached.
**Rule:** parsers of tmux or terminal output must be total, never raising on
unexpected bytes, and a failed reader must detach its tmux client. Changes to
the terminal/tmux stream path need QA against real agent panes. After every
install, soak for about 60 s: dashboard, inbox CLI, `tmux ls`, and the server
log clean. Roll back immediately if any of them fails.

Also: the e2e harness shares `$TMPDIR/rd-e2e-state.json` across concurrent runs,
so parallel sessions' gates overwrite each other's token (all-401 failures).
Set `RD_TEST_STATE_FILE` and `RD_TEST_PORT` per run until the harness isolates
itself.

## 2026-09-28 — Load rules before accepting edits

The AGENTS.md editor allowed Add while its initial GET was pending. The response
replaced the new rule, then Save reported success for an empty list. Gate edits
and saves on successful loading, ignore stale responses, and block saving after
load failures. Test delayed responses and Enter as well as clicks; a passing retry
does not explain an intermittent failure.

## 2026-09-28 — Analytics must preserve the model at usage time

Harness names are not model identities. Claude assistant records carry model IDs,
while Codex model metadata lives on turn-context lines without token usage. Read
both record types, bucket deltas under the then-current exact model, and retain an
explicit unknown bucket. Tests cover model switches, duplicate blocks, incremental
rescans, UTC dates and unmapped identities. Never average daily medians: combine
histograms and keep the result labelled approximate.

## 2026-09-28 — A local gate must include the checks that can reject CI

Mail analytics passed the local gate but failed CI strict typing because the
gate omitted mypy. Add the CI Python checks (mypy, Black and slop_check) to the
local gate, and type aggregate keys, counters and iterators explicitly. A green
subset of checks must not be reported as covering the omitted CI checks.

## 2026-09-28 — Preserve counts at the same boundary that deletes their source

Seven-day mail retention cannot support lasting analytics by querying live rows
alone. Transfer aggregate counts in the same transaction as deletion, including
session removal and Oracle event retention; query remaining live rows separately.
Fault-inject deletion to prove a failed transfer leaves neither missing nor double
counts. Keep completion-day activity separate from sent-day cohorts, and label
histogram percentiles approximate rather than deriving fake medians from totals.

## 2026-09-28 — Trace the thing before designing the fix for it
**Broke:** B2 was filed as "connectors configured but not usable", and two
rounds of design went into a setup wizard for connectors that could not be
set up.
**Cause:** nobody had run the connectors. Speaking MCP through the exact
command the harness configs use (`duckterm connector-run NAME`) returned
GitHub 45 tools, Railway 34, Porkbun 25 — all three working. The defect was
that the panel could only say `enabled=True, detail=None`, which asserts a
config entry, not a usable tool. The owner's "I don't know if I can really
use them" described the UI precisely.
**Rule:** before designing a fix, exercise the feature end to end through the
real path a user's software takes, and let the result pick the fix. The
evidence a trace produces is often the feature itself: here the probe became
the Check now action. Corollary for probes — hold stdin open until the reply
lands, because closing it early makes an MCP server exit with "server is
closing: EOF", and a working connector reads as broken.

## 2026-09-28 — A shared directory cannot identify a conversation

Codex Resume used the newest rollout in a cwd when its recorded native ID was
missing or stale. Two sessions in one repo could silently exchange conversations.
Use only the native ID recorded by a session-key-bound hook; never replace it
with a directory guess. Refuse unknown identity when another Codex row shares
the directory, including stopped rows. Test independent IDs through a database
restart and keep fork/snapshot paths from reintroducing the same fallback.

## 2026-09-28 — Move action behavior and verify adjacent views

Moving session actions out of the sidebar must retain lifecycle gates, remote
routing and delete confirmation, while making the card independent of density.
Keep only stopped-row Resume as a quiet recovery shortcut. Use distinct styles
from the Inbox session card, and dismiss action menus after
Stop so they cannot cover Resume. Exercise both the moved actions and Inbox.

## 2026-09-28 — Saved comments need a safe read-and-render path

Persisting a comment alone makes previous feedback invisible. Fetch annotations
alongside messages and after saving, match rendered text nodes rather than HTML
strings, and preserve overlapping/inline-formatted quotes. Count unlocated notes
against the whole transcript; notes on older turns are not missing. Keep saved
notes readable when their original text disappears and expose notes on focus.

## 2026-09-28 — An idle agent cannot notice inbox mail by itself

New peer mail waited behind both an idle grace and an old open item, while
reading an unfinished question excluded it forever. Skip the peer-age delay
for idle recipients, track reminded IDs independently of newer arrivals, and
allow one delayed reminder for read queued questions. Persist the per-item
history across server restarts; retain draft, active-turn and owner-typing guards.

## 2026-09-27 — Put keyboard tooltip behavior on the actual focus owner

Oracle Needs-you buttons own focus so their nested remote duck adds no tab
stop. The mascot-only focus selector left the machine tooltip hidden during
keyboard navigation. Let the parent button reveal it and test with the mouse
away; a hover check alone can hide this accessibility gap.

## 2026-09-27 — Session location must follow the duck into Oracle

The sidebar cloud did not reach Oracle because its fleet ducks rendered the
base mascot directly. Reuse the location component across both views, preserve
Oracle's button focus and activity poses, and cover remote disconnection and
recovery while Oracle stays open. A sidebar-only test missed this surface.

## 2026-09-27 — Restoring selection must respect collapsed startup folders

Restoring a saved session dispatched the same folder-reveal event as a new
launch, reopening its parent folders despite the existing collapsed-startup
behavior. Keep selection restoration independent of folder expansion. Select a
nested row explicitly before reload in regression tests; relying on the default
selection made the failure depend on which session arrived first. Native checks
must verify the selected context even while the corresponding row is hidden.

## 2026-09-27 — Mark the remote exception without decorating every local row

The location preview added a computer badge to each local duck; the owner
wanted only remote sessions decorated. Keep local ducks plain and cloud remote
ducks. Removing the redundant badge preserves the approved compact layout,
existing activity animations, and machine-name tooltips.

## 2026-09-26 — Compact identity must still identify the machine

Repeated host labels squeezed session names after local and remote sessions
shared the sidebar. Put location in the existing mascot, preserve its activity
animation, and expose the machine name on hover and keyboard focus. Keep a
visible disconnected mark and an accessible label; update outage acceptance
checks when status moves from row text into an icon. Test equal session IDs on
different hosts and recovery without duplicate rows.

## 2026-09-27 — Selection restoration must not reopen folders

Restoring the saved session dispatched the same folder-reveal event as an
explicit launch, undoing the collapsed-on-restart sidebar default. Restore the
active session independently from folder expansion. Regression tests must select
a nested session before reloading and verify both its restored selection and
collapsed ancestors; merely opening folders leaves this dependent on timing.

## 2026-09-26 — A visible button can still be outside the window

Adding Pin to the Context header crowded the reopen arrow out of its 36px
collapsed rail. Hide every non-toggle header child when collapsed, including
future actions. Browser visibility assertions missed this because an offscreen
button still has a layout box. Assert viewport/rail bounds and hit-test the
center before clicking, with both Pin and Pinned labels and narrow layouts.

## 2026-09-27 — Hidden terminals still parse output

Keeping every terminal mounted makes switching fast but gives every hidden
agent a parser competing with foreground keystrokes. Bound the recent-view
cache, leave server-owned PTYs running, and reconnect older views through the
existing snapshot path. Measure at fleet scale and test an unfinished input
line across eviction; a mount-count assertion alone cannot prove safety.

## 2026-09-26 — Re-read the inbox between merge and tag
**Broke:** the owner deferred F12 work tracking ("keep this in the roadmap… I
want to think it through") while its release PR was in CI. release-dev merged,
tagged and installed v0.4.72 without reading the hold that had arrived meanwhile,
so F12 shipped and the DB moved to schema v5. v0.4.73 carried it too.
**Cause:** release-dev checked the inbox only at the start of a release, not
before each irreversible step (merge, tag, install). A schema bump made the
mistake one-way: dropping back to v4 would raise SchemaTooNewError.
**Rule:** check the inbox (and pending replies) immediately before merge, before
tag and before install. A revert of a schema-bumping feature keeps its
`_SCHEMA_VERSION` and leaves its tables in place (#84 reverted, schema stays 5).

## 2026-09-26 — Expand the existing preview without replacing its document

A full-window artifact view must preserve the iframe, scroll position, selected
artifact and feedback draft. Keep the viewer mounted, restore background access
and opener focus after returning, and route Escape from the sandboxed frame
through the same source/origin/nonce checks as selections. Moving Feedback into
the viewer also makes its React key a sibling of the preview key: namespace them
separately, or closing feedback can leave duplicate previews after revisions.
Cover selection, revisions and keyboard return together in a real browser.

## 2026-09-27 — Restoring rows does not restore selection
Native acceptance restored remote rows and grouping but missed the selected
row after quitting the app. Persist machine plus session ID at selection time.
Local data arriving first must not overwrite an offline remote selection; only
a successful snapshot from that host can establish that the saved row is gone.
An explicit user choice cancels the pending restoration. Verify an actual app
relaunch as well as reload, and make failed acceptance assertions fail the run.

## 2026-09-26 — Changing how a note is answered means changing every place that says how
**Broke:** after menu notes moved to "answer in the terminal" (#69), the
control tower's Needs-you list still said "Answer in the chat", and menu rows
were blank because their questions live in a list, not in `question`.
**Rule:** when a note's answer route or shape changes, grep every surface that
renders that note kind (chat, control tower, notifications) in the same PR
(#91).

## 2026-09-26 — Stalled work needs bounded reminders

A cooldown alone still wakes an idle agent forever. Back off after the first
reminder, stop after the second without progress, and notify the requester.
Persist the count across restart and share it with task-end notices. Keep
status maintenance outside the Oracle kill switch.

## 2026-09-26 — Acceptance must reconcile pre-created work

Work can be explicitly tracked before its inbox request is accepted. Reusing
the existing ID is not enough: acceptance must advance its proposed state,
while preserving later progress and any intervening reassignment. Cover both
creation orders and retries, including requests without work-title metadata.

## 2026-09-26 — Closing a message must not erase the work

An inbox reply records a conversation, not an outcome. Keep assigned work in
its own durable record, require completion evidence or a named blocker, and
return declined/unavailable assignments for reassignment instead of deleting
them. Closing an old message after a handoff must not unassign its successor.
Store work-reminder timestamps in SQLite so restarting Oracle cannot repeat
an hourly reminder; use fixed reminder text rather than copying peer content.

## 2026-09-26 — Instruction files written once go stale, and nudges must say what to do
**Broke:** 15 of 27 sessions' collaboration.md still said questions expire
after five minutes, so long-running agents passed --timeout and their work
orders silently expired. Separately, the "continue your work" reminder dropped
both the peer-authority line and any fallback for an unsure session.
**Cause:** session-instructions/*/collaboration.md is written only when a
session is introduced. The reminder wording was tuned for one failure (agents
stopping) and lost the other guardrails.
**Rule:** refresh generated per-session guides at server startup
(refresh_guides). An automated nudge states the remit, the authority boundary
and what to do when unsure, in one line each (#77).

## 2026-09-26 — Assert on the pid that matters, and prove the assert can fail
**Broke:** `test_shutdown_stops_credential_holding_descendant` failed on CI
with a heartbeat write ~100 ms after the sample point. The first fix waited
for `os.killpg(proc.pid, 0)` to raise `ProcessLookupError` — and failed on CI
again, the same way.
**Cause:** that wait was a no-op. `proc` is the supervisor, which pytest
starts *without* `start_new_session`, so it is not a group leader and no
group has its pid; `killpg` raised `ProcessLookupError` on the first call and
the loop never waited. The group that `run()` actually signals belongs to the
MCP server (`start_new_session=True`), whose pgid is the inner pid the test
never saw. A green local run proved nothing, because the assertion could not
fail.
**Rule:** when a test asserts "process X is gone", make the fixture report
X's real pid and group rather than deriving them from a handle that may not
be the leader, and include them in the failure message so a CI-only failure
is diagnosable from the log. Before believing any such fix, mutate the
fixture so the process *does* survive and watch the test go red — an
assertion never seen failing is not evidence. Local reproduction attempts
(12 CPU hogs on 14 cores, then 2x oversubscription with `taskpolicy -b`) all
stayed green while CI stayed red, so treat "cannot reproduce" as a reason to
strengthen the assertion, not to ship the explanation.

## 2026-09-26 — Session pin limits belong in the same write as the pin

A browser-only three-pin cap cannot protect against two windows taking the
last slot simultaneously. Persist session pins and enforce the count in one
conditional SQL write. Treat polling metadata as authoritative over stale
local pins, and keep stopped or archived sessions in Focus rather than
silently changing the owner's selection. Check real terminal input as well
as layout persistence; a mock terminal is not evidence of an interactive one.

## 2026-09-26 — A guessed context window must not render like a fact
**Broke:** the context row showed "559k used · 0 left" for claude-opus-5
sessions, which have a 1M window, and every opus-5 session carried a false
"high" context warning from 160k. 11 of 15 sessions with a recorded model were
affected.
**Cause:** `MODEL_WINDOWS` matched only fable|mythos. opus-5 fell back to the
200k default, and `max(0, window - used)` silently clamped the impossible
result to zero.
**Rule:** an assumed value renders as unknown, not as a number. Used exceeding
the assumed window proves the guess is wrong. Never drive a warning from a
guessed denominator (#82).

## 2026-09-26 — In-memory Oracle state is lost on every release install
**Broke:** each server restart, including every release install, could paste
a duplicate "you have N inbox items" reminder into every idle agent that still
had the same unread mail.
**Cause:** Oracle kept its last nudge per session only in `_oracle_nudges`.
**Rule:** anything that suppresses repeat typing into an agent must survive a
restart. Record it in history (`OracleNudge.mail_ids`) and rebuild from there
(#75).

## 2026-09-26 — The Mac app's navigation filter must allow srcdoc subframes
**Broke:** every Markdown artifact preview was a blank box in the Mac app,
while the same page rendered in a browser at localhost:4300.
**Cause:** DashboardWindow's navigation policy cancelled anything not on the
dashboard's http host. Sandboxed srcdoc previews load as `about:srcdoc`, so
WebKit's iframe load was refused. Browser tests can't see this: they never run
the native policy.
**Rule:** web features that add iframes, blob URLs or new schemes need a check
in a real WKWebView behind the production policy
(`scripts/test_artifact_preview.sh`), not only Playwright.

## 2026-09-26 — Keep inbox context separate from the message reading area

An expanded session card and onboarding panel looked like inbox messages and pushed actual mail below the fold. Give session context its own collapsed summary and move setup into an explicit tools dialog. Bound the message list through the complete flex layout so it scrolls independently of the heading and controls. Adapt to the pane's width, not just the window, and verify a populated list, long expanded replies, live refresh, and the smaller folder modal. Search and filter counts must describe loaded records, not imply a complete server-side search.

## 2026-09-26 — Inbox reminders must not stop ongoing work

Oracle's hardcoded “then stop” turned an inbox reminder into a new instruction
to halt, even when the owner had already authorized unfinished work. Agents
repeatedly acknowledged mail and went idle. Keep reminders focused on checking
messages and continuing work; do not add workflow restrictions to an automated
nudge. The delivery regression checks the actual pasted continuation wording
and still verifies that peer message text is not injected into the reminder.

## 2026-09-26 — Don't answer an agent's menu by pressing keys
**Broke:** release-dev asked two questions in one form. Oracle's chat showed
only the first. The owner approved it, Oracle pressed "1", the form moved to
the second question, and nothing was submitted.
**Cause:** the relay read only the first question, and a digit press answers
whatever menu tab happens to be on screen.
**Rule:** menu notes list every question and link to the session's terminal.
The owner answers there. Relaying answers needs a channel that doesn't type
into the terminal.

## 2026-09-26 — A launch destination must not replace the dashboard
**Broke:** creating a remote session replaced the local dashboard, hid live local
agents, and skipped folder assignment. Repeated failed launches left dead rows.
**Cause:** the launch form changed, but feeds, terminal connections and actions
still assumed one selected host. Browser tests mocked the native switch.
**Rule:** keep one local dashboard and route by machine plus session identity.
Validate the real native path with simultaneous local/remote agents, a quiet
remote terminal, local launch from remote selection, and a remote outage. Verify
that existing rows, terminal DOM and agent processes survive. Keep spawn errors;
reject predictable failures before creating rows.

## 2026-09-26 — Empty folders need their own refresh path

The sidebar rendered saved empty folders, but fetched their catalog only on mount, session-count changes, or successful local edits. A folder created or moved elsewhere could remain invisible indefinitely, and a failed move reported an existing destination without revealing it. Refresh the folder catalog independently and on focus/return; refresh after a move conflict too. Preserve rows on fetch failure and ignore superseded responses. Browser coverage must create and move empty nested folders after the page is open and verify persistence after the last session leaves.

## 2026-09-26 — A checkout picker must select the checkout itself
**Broke:** destination Browse silently appended a child name and reused DuckTerm's
folder after switching to Sotto. Users could neither select their exact empty
folder nor create a named folder in the picker.
**Rule:** bind clone destinations to their repository, clear stale selections on
repository changes, and choose the exact checkout folder. Offer explicit folder
creation. Publish into selected empty directories atomically; never merge over
existing files, including files arriving between review and publication.

## 2026-09-26 — Typing into an agent needs proof it submitted
**Broke:** an Oracle nudge sat typed but unsent in a Claude Code prompt for
14 hours. The leftover text then blocked every later nudge to that session.
**Cause:** the paste and its Enter went out as one write, and Claude Code
sometimes swallows an Enter that arrives with the paste. About 1 in 40 nudges.
**Rule:** paste, pause, then press Enter separately. Confirm with the agent's
`UserPromptSubmit` event, retry Enter once, and report "stuck" instead of
claiming delivery.

## 2026-09-26 — Destination folders need browsing too
**Broke:** entering a guessed remote home path left cloning blocked on a missing
parent directory. Example paths were mistaken for real destination values.
**Rule:** browse the selected machine's actual home and choose an existing parent,
then suggest a new project subfolder from the repository or source name. Reuse
the folder explorer, recover from invalid drafts, and clear review consent when
the destination changes.

## 2026-09-26 — Launch must wait for project preparation
**Broke:** choosing a GitHub repository left Launch enabled before a destination
was entered or the clone completed, producing a generic missing-folder error.
**Rule:** copy and clone sessions can launch only after preparation succeeds.
Check the incomplete form, reviewed form, active transfer, and ready state for
both local and remote destinations; happy-path clone coverage alone missed this.

## 2026-09-26 — Repository selection and cloning must share connector authorization
**Missing:** New Session only offered cloning remotely, required a URL, and used
Git credentials unrelated to the account shown by the GitHub connector.
**Rule:** expose cloning for This Mac and remote destinations. List repositories
through the destination's selected connector and use that same grant to clone.
Shared credentials stay on the connector host; verified repository bundles cross
the authenticated relay. The broker must own temporary-directory cleanup so
revoking access also removes partial clones. Never fall back silently to an ambient account.

## 2026-09-26 — Copy source needs a folder explorer
**Broke:** copying a local project to a remote machine required typing its path.
**Cause:** the source form did not reuse New Session's existing folder browser.
**Rule:** offer Browse beside the source field and route it explicitly to This
Mac even when the launch destination is remote. Reuse the existing explorer and
verify that choosing a folder feeds the transfer review.

## 2026-09-26 — Unknown output must not invent a state transition

The supervisor classifies individual output lines. Defaulting an unmatched line
to idle (Codex/Copilot) or busy (Claude/generic) turned harmless output and ANSI
redraws into fake Stop/PreToolUse events. Return no evidence instead and retain
the last known state. Test the full marker → ordinary output sequence on both
tmux and PTY; testing a marker alone misses this failure. Ten regression cases
failed on old main and passed with the fix. Positive regex false matches and
hook precedence remain separate investigation items.

## 2026-09-26 — Artifact feedback needs provenance and an isolated selection bridge

Text selected inside an opaque preview cannot be read directly by the app. Keep that origin isolation; authorize only a small app-owned reporter with a fresh CSP nonce, strip artifact scripts, and validate the sending frame/channel. Bind feedback to the saved artifact revision, escape terminal controls, and retain failed comments instead of reporting a false send. Verify real terminal receipt and malicious-preview rejection together.

## 2026-09-26 — Recheck layout integration after concurrent merges

The Control Tower introduced a wrapper between workspace and panels while collapse controls passed their branch checks. A direct-child CSS selector then stopped applying, and Oracle labels changed. Match the panel through its workspace ancestor, update the integration test to the merged UI, and gate the exact combined tree before publishing.

## 2026-09-26 — Collapsing chrome must preserve the live terminal

Side panels consumed space even when the owner only needed the center. Collapse their contents without unmounting the terminal or changing its session identity; leave a visible, keyboard-accessible reopen control. Verify real PTY resize frames, unchanged unsent input, independent toggles, persistence and Oracle interaction, not just a wider CSS box.

## 2026-09-26 — Artifact previews must outlive temporary source files
**Found:** a generated report or mockup can live in a temporary directory, so a
catalog of file paths loses the deliverable when the agent cleans up or a file moves.
**Rule:** register a bounded saved copy using the generating session's credential.
Treat the path as provenance, never an instruction for the server to read a file.
Keep artifact content out of the app's HTML origin, and test source deletion,
session isolation, replacement, cleanup and backup restore before shipping.

## 2026-09-25 — Last-agent exit raced the next tmux launch

Linux fork-chain CI intermittently returned HTTP 400 because tmux reported
`server exited unexpectedly`, reproduced after 30 passing repetitions. Its
last short-lived agent could exit while the next launch connected. Configure
`exit-empty off` on DuckTerm's private server before launching the child in
the same tmux command queue. Preserve HTTP error bodies in fork tests: a
passing retry is not diagnosis. Test that the server PID survives an empty
interval and the next launch, and stress the real Linux fork chain.

## 2026-09-26 — Re-adopting a terminal must repair stale interruption state

All 21 live terminals survived while their database rows said interrupted, exposing Resume and disabling session messaging. Startup reattached panes but never cleared an existing interruption; normal hooks deliberately preserve at-rest states. Recover only confirmed-live interrupted sessions from their latest agent activity, clear ended_at, and restore enrollment without relaunching or inventing a new run. Keep deliberate Stop/Archive states intact. Failed tmux discovery is unknown liveness, not an empty fleet: never interrupt everything on a PATH/socket error. Verify stored state and process continuity after release, not only pane counts.

## 2026-09-25 — Density is information structure, not just font size

The first Compact/Standard/Relaxed preview only varied padding and type size, so the modes looked alike. The approved design changes one-line versus two-line rows, hover details, and persistent selected-session controls. Keep session names regular-weight in every mode, remember the choice, and test both geometry and access to hidden actions. Preserve the existing row-selection focus behavior: adding a focusable wrapper stole focus from the newly opened terminal, caught by the full browser suite.

## 2026-09-25 — Opening Oracle must not resize a live terminal

Oracle used to consume workspace width. A long active input line was permanently clipped after an open/close cycle, even though the original terminal dimensions returned. xterm excludes the cursor line from normal reflow; a resize-back is not a repair. Keep Oracle over the existing context column, hiding its covered controls, and preserve the terminal geometry. Browser regression covers long unsubmitted input, repeated toggles, and typing afterward. Completed-output-only resize tests missed this case.


Append-only. One entry per issue we actually hit: what broke, the root cause,
and the rule that prevents the recurrence. Newest first.

## 2026-09-26 — Oracle showed Codex's auto-approved commands as approval notes
**Broke:** the chat filled with "Approval" notes for feature-remote-session
commands that ran seconds later without the owner.
**Cause:** Codex fires PermissionRequest for every gated command, and with
`approvals_reviewer = "auto_review"` its reviewer approves nearly all of them
(1,597 requests in 5 days). The relay treated each request as the owner's to
answer. The hook also registered a waiting approval for Codex, which kills
hooks after 3 s, so those records stayed "pending" and their notes open.
**Rule:** a signal that something *might* need the owner isn't evidence that
it does. Look for the agent actually asking (its prompt on screen, a hook
that is really waiting) before surfacing it, and check the per-runtime event
history before assuming an event means the same thing for every agent.

## 2026-09-26 — Initial terminal activation must respect open menus
**Broke:** New Session intermittently vanished while being clicked, even without
concurrent native UI probes.
**Cause:** late session discovery activated the default terminal through an
unconditional focus call, bypassing the guarded replay path.
**Rule:** only explicit session-row or view-tab navigation may override another
control's focus. Delay initial session discovery in a browser regression and
verify an already-open menu remains focused and usable.

## 2026-09-26 — Move must preserve the name and open the exact destination session
**Broke:** full native Move acceptance showed the destination folder name instead
of the source session name. Opening the remote dashboard also lacked the moved
session's identity, so it could select another session.
**Cause:** transfer launch did not persist the display name through history's
metadata API, and host switching carried only launch drafts.
**Rule:** persist the name and carry the exact destination key through the native
bridge. Verify the selected row with multiple sessions and the durable source
link through the actual desktop flow, not only transfer API tests.

## 2026-09-26 — Artifact downloads and remote navigation share one delegate policy
**Found:** integrating artifact downloads introduced a second navigation-policy
callback alongside the remote dashboard's origin restriction.
**Rule:** combine download and navigation decisions in one callback. Only the
current dashboard's main frame may download a blob, and changing computers must
reset navigation-load state so a failed new connection can retry. Compile the
native app and rerun artifact, report UI, and transfer checks after integration.

## 2026-09-26 — "Waiting" badges stayed wrong for days after the idle fix
**Broke:** 6 sessions showed "waiting" and filled the control tower's Needs
you list; 5 weren't waiting on anything.
**Cause:** the 2026-09-23 fix made new idle notices derive "idle", but
sessions whose last event was an old untyped idle notice kept "waiting".
State only changes on new events, and an unused session sends none.
**Rule:** when a fix changes how state is derived, also correct the state
already stored. Here Oracle's minute check clears "waiting" when the screen
shows an empty prompt and no approval is pending.

## 2026-09-26 — Control tower opened with its header scrolled off screen
**Broke:** opening the tower showed the tiles cut off at the top and no
"← Sessions" button, so there was no visible way back.
**Cause:** the Oracle chat kept its newest answer in view with
`scrollIntoView`, which scrolls every scrollable ancestor, so it scrolled the
whole tower page down. The mocked-chat screenshots before release had an
empty chat, so nothing needed scrolling.
**Rule:** keep a list at its bottom by setting that list's own `scrollTop`.
Check a page with realistic content in every scrolling region before
shipping it.

## 2026-09-26 — Replacing the panes with the control tower broke terminal wrapping
**Broke (caught by e2e before merge):** after opening and closing the control
tower, every long line in a terminal wrapped one column later than before.
**Cause:** the tower first replaced the three panes, which unmounted the
terminal. Remounting replays its output, and the replay wrapped at a
different width than the live session had. The B5 regression test
(`oracle-terminal-resize.spec.ts`) failed on the rendered rows.
**Rule:** a full-page view goes over the panes as an opaque layer, with the
panes kept mounted and `inert`. Never unmount a terminal just to show
something else.

## 2026-09-26 — Token totals were inflated 6x in the control tower design
**Broke:** the design and prototype said 10.9B tokens in 7 days; the real
figure was 1.8B.
**Cause:** Claude Code writes one transcript line per content block and
repeats the reply's usage on each, so summing lines double-counted. The scan
also summed whole transcripts touched this week, including months of older
history in resumed sessions.
**Rule:** count Claude usage once per (message id, request id) and bucket by
each record's own timestamp. Check a derived total against one file counted
by hand before putting it in front of the owner.

## 2026-09-25 — Ask Oracle reported prompt suggestions as the owner's instructions
**Broke:** Oracle told the owner that qa and bugs-dev were both "told to
deploy the fixes to production" and warned they might deploy twice, and that
architect was sitting on "approve the VM testing". Nobody sent those.
**Cause:** the fleet digest read tmux panes without escapes. Claude's dimmed
suggested next prompt and Codex's placeholder then looked like typed input.
**Rule:** anything that reads an agent's screen for meaning must drop dimmed
text on the input line, the same way Oracle's empty-prompt check does.

## 2026-09-25 — Oracle never nudged a session that existed before a restart
**Broke:** zero nudges in two days, while main-qa sat idle with five unread
peer messages and an empty prompt.
**Cause:** reconcile() re-adopts surviving tmux panes with GenericRuntime, and
Oracle asked the supervisor's runtime whether the prompt was empty. Generic
always says no. Tests used a fake supervisor carrying the real runtime, so they
never saw what a restarted server holds.
**Rule:** per-harness behavior for a session resolves from its DB row's
runtime, not the supervisor's. Test fakes should mirror the adopted state,
not the freshly launched one.

## 2026-09-25 — Remote-session integration must preserve newer native and launch behavior
**Broke:** both branches added the same navigation delegate callback; newer launch
properties also left the feature's tests stale, and standalone native tests lacked
remote-host dependencies.
**Rule:** combine callbacks, preserve folder-assignment and launch-selection behavior,
keep the stable installed bundle IDs, and run both full application and native UI
checks after integrating main. Test builds must retain their isolated identity.

## 2026-09-25 — Restart should not expand every folder
**Broke:** every dashboard mount initialized folders as expanded, so restarting
filled the sidebar with all sessions.
**Rule:** initialize folder headers collapsed, including nested folders. Verify
reload closes previously opened folders and that reopening still reveals their
sessions; keep header actions available while collapsed.

## 2026-09-25 — Changed-file backups need filtered staging and restore checks
**Found:** syncing raw agent directories bypasses archive exclusions, and size or
mtime alone can miss a same-size transcript rewrite. A mutable remote tree also
cannot promise historical recovery with an older database snapshot.
**Rule:** stage the existing filtered archive, compare checksums, retain whole
uniquely named SQLite copies and local archives, and publish completion last.
Keep full archives as the default historical backup. Verify unchanged and changed
uploads, excluded secrets, retained deleted paths, and a real cloud restore.

## 2026-09-24 — Bookmark shortcuts should stay compact
**Broke:** bookmark excerpts filled the terminal strip instead of the owner's
requested pin-only links; a colored emoji also ignored the neutral-color request.
**Rule:** use a monochrome SVG that inherits theme text color, keep the visible
shortcut icon-only, and put a short excerpt in the hover label. Retain a descriptive
accessible name and exact-message navigation.

## 2026-09-24 — Message bookmarks must preserve message boundaries
**Found:** the Messages turn view flattened assistant messages into text blocks,
losing the identities needed to pin one response or jump back to it.
**Rule:** retain message records inside each turn. Resolve a pin by its checked
key on every refresh, keep the selected turn stable as new turns arrive, and
render its saved snapshot if the original changes. Browser acceptance must
exercise real transcript rewrites, reload, removal, and an unsubmitted terminal
draft, not just button visibility. Keep internal tool records in their compact
summary; adding bookmarks must not expand every tool call into another row.

## 2026-09-24 — Saved messages need content checks and retained copies
**Found:** Messages uses transcript line positions, which can point to different
content after a transcript rewrite. Persisting that position alone would make
a bookmark silently jump to another message.
**Rule:** verify the content and conversation as well as the position. Retain a
snapshot at pin time; when the exact reference disappears, show the saved copy
rather than reusing the old line number. Test rewrites, repeated text, restarts,
and session-scoped removal.

## 2026-09-23 — Terminal attachment must preserve menu focus
**Broke:** a terminal finishing its connection stole focus from Settings and
closed the menu before its action could be clicked.
**Rule:** asynchronous terminal attach/replay may focus an unoccupied page or
the terminal itself, but must preserve focus in other controls. Only explicit
terminal selection or clicks may take focus from another control.

## 2026-09-23 — Connector status must not block the dashboard
**Broke:** backup settings intermittently stayed disabled during initial loading
in CI. Connector status ran credential and CLI probes on the server event loop.
**Rule:** run synchronous connector probes in a worker thread. A regression must
hold a probe open and verify another dashboard request completes before it does;
increasing the browser timeout would leave the responsiveness bug intact.

## 2026-09-23 — Product renames must preserve the installed application identity
**Broke:** the pending native rename changed the bundle identifier, leaving the
configured support preference behind in the old defaults domain.
**Cause:** a display-name rename also replaced the persistent application ID.
**Rule:** keep the installed bundle identifier stable while changing app,
executable, and display names. Verify existing local preferences remain available
and never embed private support configuration in published release assets.

## 2026-09-23 — Header actions need clear grouping and labels
**Broke:** separate creation, theme, backup, and harness controls crowded the
header, and an unexplained bell concealed the notification setting.
**Rule:** group creation under New and preferences under Settings; retain the
owner-requested AGENTS.md shortcut separately. Use a labelled notification
control, preserve existing actions and theme persistence, and verify keyboard
focus, dismissal, and menu bounds alongside each relocated browser flow.

## 2026-09-23 — Idle Claude sessions reported as waiting
**Broke:** 8 of 19 sessions showed "waiting", some for 50+ hours, and the tab
title counted them as needing an answer. Most were idle at the prompt.
**Cause:** Claude Code sends a Notification about 60 seconds after a turn ends
with nothing to answer. The hook dropped `notification_type`, and every
Notification derived `waiting`.
**Rule:** forward the fields that tell event subtypes apart before deriving
state from an event type. Check the live DB against the dashboard when a badge
count looks too high to be true.

## 2026-09-22 — Attach scrolling misses terminals opened from hidden slots
**Broke:** opening an existing session still displayed old scrollback after the
first-frame scroll fix. Sessions stay mounted while hidden, so switching sessions
or returning to Terminal does not trigger another attachment.
**Rule:** treat visible activation separately from connection. Fit the visible
pane, wait for parser/layout, then scroll once; invalidate stale callbacks and
cancel on user navigation. xterm skips scroll events at an unchanged buffer
bottom: explicitly synchronize its public scroll API after the reflow frame so
the DOM scrollbar cannot retain an older buffer height. Never fit hidden slots
or scroll on ordinary output
or resize. Test hidden-session switching and Messages-to-Terminal reopening.

## 2026-09-22 — Clipboard filename text is not the copied image
**Broke:** pasting screenshots could insert only a filename, leaving agents unable
to read the image. Native paste preferred text and ignored Finder file URLs;
browser handling ran after xterm's text paste handler.
**Rule:** resolve image/file representations first only for terminal targets,
capture browser image paste before text handlers, and report save/decode errors.
Check readable bytes, mixed clipboard data, normal editor paste, and actual
Claude Code/Codex image reads. A local path does not prove remote attachment support.

## 2026-09-22 — Completed helper history must not crowd out active work
**Broke:** completed helpers accumulated as permanently expanded sidebar rows.
**Cause:** active and completed helpers shared one unconditional list.
**Rule:** leave active helpers visible and retain completed rows behind an
accessible per-session count, collapsed by default. Check independent keyboard
expansion with real counts and preserve prompts when running helpers finish.

## 2026-09-22 — Manual backup UI must keep job state separate from a click
**Broke:** the released backup API had no owner-facing controls or visible result.
**Cause:** backend delivery was treated as the feature while its approved UI waited.
**Rule:** expose the remembered destination and actual background-job result;
never start on open, guard duplicate clicks, and confirm status after a lost POST
response. Exercise local archives with isolated transcript roots, never real
transcripts or a cloud upload in browser tests.

## 2026-09-22 — Attach positioning must not become continuous auto-scroll
**Broke:** attaching to a terminal could leave its viewport above the latest output.
**Cause:** xterm parsed the replay asynchronously with no explicit attach position.
**Rule:** scroll once after the first replay frame is parsed, cancel if the user
starts navigating, and reject callbacks from old connections. Never scroll on
ordinary writes or resize. Verify long history, manual scrolling, and later output.

## 2026-09-22 — Completion feedback must distinguish live transitions from replay
**Broke:** session ducks had no completion feedback; naive animation on idle state
would celebrate historical sessions every time the page loaded.
**Cause:** persisted state and SSE replay are not newly witnessed turn completion.
**Rule:** detect live turn transitions centrally, suppress seeds/replay/repeated
states, expire feedback after four seconds, and disable jumps for reduced motion.
Stop ends the turn immediately even while the existing busy display grace settles.

## 2026-09-21 — Manual backups must not block the dashboard
**Broke:** the shipped backup CLI had no owner-controlled dashboard operation.
**Cause:** archive creation and cloud upload are synchronous, while the dashboard
needs a remembered destination and a visible outcome.
**Rule:** run the existing archive operation in one background job, persist its
configuration/result privately, reject overlap, and expose the retained local
path on upload failure. Never silently choose a destination or retry on restart.

## 2026-09-21 — Owner notices need distinct lifecycle labels
**Broke:** folder broadcasts inherited question labels and had no owner send UI.
**Cause:** the inbox assumed every entry was a peer question awaiting a reply.
**Rule:** derive Owner from server sender kind, distinguish unread from notice
shown, review eligible recipients, and preserve the request key on send retries.

## 2026-09-21 — Native dialogs need their own clipboard and save lifecycle
**Broke:** the handed-off report form inherited dashboard-only copy/paste actions
and could open overlapping save operations or close while export used its files.
**Cause:** adding a second WebView without routing Edit actions to the active
window or holding a busy state across native file-picker callbacks.
**Rule:** route clipboard actions to the active editor, guard the full picker and
export lifecycle, and verify the real WKWebView form, cancellation, ZIP output,
opt-outs, keyboard dismissal, and narrow-window layout before shipping. Successful
Save closes the report and reveals the ZIP; failures keep the form available.
Automate the destination callback rather than invoking unsupported NSSavePanel
actions that can strand a test window.

## 2026-09-21 — Folder messages need an owner identity and inbox delivery
**Broke:** a folder message had no durable owner-to-session delivery path.
**Cause:** peer questions require a sending session and terminal input can race
drafts or running work.
**Rule:** authenticate the owner at the route, fan out durable notices with an
explicit sender kind, and keep peer quotas and unread state separate. Retrying
a send must return the original delivery result without duplicating notices.

## 2026-09-21 — Closing old persistent work needs its own timestamp
**Broke:** cancelling a month-old persistent assignment made cleanup remove it
before the cancellation response could be returned.
**Cause:** cancellation had no closing timestamp, so retention used creation time.
**Rule:** timestamp every closing transition and retain its history from closure.
Cover aged requests, not only newly created ones.

## 2026-09-21 — Idle agents never saw short-lived inbox assignments
**Broke:** QA and implementation handoffs expired before recipients read them.
Acknowledging a request did not extend its deadline, and delivery only updated a
badge; it never scheduled an agent turn.
**Cause:** short-lived question semantics were used as a work queue, while agents
were expected to notice and poll it themselves.
**Rule:** retain assignments by default and make deadlines explicit. Use supported
turn-end hook feedback for reminders, with durable repeat suppression and no
terminal writes. Retain unhandled work when notification is unavailable.

## 2026-09-21 — Folder actions were missing from session workflows
**Broke:** creating a session from the global button offered no sidebar folder
choice, and folder headers had no control for interaction history.
**Cause:** folder assignment existed only as an implicit launch preset. The old
folder-history implementation remained on `feat/folder-conversations` and was
not an ancestor of the current release; the release exposed only session inboxes.
**Rule:** keep folder history visible independently of pending counts, hover,
collapse, or live-session filters. Land fixes on main before releasing so a
later release cannot silently omit a feature branch. Cover folder selection and both directions
of folder exchanges with end-to-end regression checks.

## 2026-09-21 — Stop silently discarded pending collaboration
**Broke:** stopping sessions during a restart cancelled owner work requests.
**Cause:** credential revocation also cancelled durable questions, and the child's
SessionEnd could arrive before the server recorded the resumable stop.
**Rule:** persist Stop before terminating the child; revoke its credential while
preserving pending questions and their original deadlines. Test late exit events,
resume with a fresh credential, expiry while stopped, and final cancellation.

## 2026-09-21 — Report attachments must match what the user reviewed
**Broke:** the first report collector checked a file's size and then performed an
unbounded read; an attachment could grow or be replaced after selection.
**Cause:** treating a selected filesystem path as an immutable attachment.
**Rule:** open without following symlinks, verify the opened file, bound the read,
and retain the selected bytes for export. Test size/count limits, opt-outs,
duplicate names, and private output permissions before wiring up delivery.

## 2026-09-21 — Codex resume defaulted to the source computer's directory
**Broke:** a transferred Codex conversation prompted to use its old Mac directory
on Linux, with that unavailable directory selected by default.
**Cause:** setting the child process cwd does not override Codex's recorded resume
directory; the launch command omitted its explicit directory option.
**Rule:** pass the reviewed destination through Codex's `--cd` option and verify
cross-platform resume against the actual supported provider version.

## 2026-09-21 — Test app packaging assumed an editable installation
**Broke:** the committed candidate imported correctly from source, but a fresh
Test build failed while calculating its isolated port.
**Cause:** the build imported the installed Duckterm package instead of reading
the helper from its own checkout; the development venv hid that dependency.
**Rule:** validate Test packaging in a fresh environment and load build metadata
from checkout files without requiring an installed application package.

## 2026-09-21 — Remote transfer and connection lifecycle need durable ownership
**Broke:** concurrent connector handshakes exceeded the connection limit; forgetting
an inactive computer left its tunnel and picker entry alive. Project migration
had only a design and could not preserve worktree state or recover a lost response.
**Cause:** capacity was checked before an await, host removal updated only storage,
and transfer/launch stages had no durable owner.
**Rule:** reserve capacity without yielding, remove cached connections with host
metadata, and journal reviewed snapshots and launch claims. Test interrupted uploads,
Git index/working-tree preservation, and lost responses before allowing migration.

## 2026-09-21 — Installation docs advertised missing downloads
**Broke:** the README recommended a nonexistent PyPI package and a Mac ZIP that
was absent from the latest release; the release guide linked an older wheel.
**Cause:** installation prose was not checked against published release assets.
**Rule:** verify the exact public wheel URL and native archive before publishing
installation instructions. State the native app's dependencies, architecture,
and signing status, and keep one canonical quick start.

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
(window.prompt) did nothing in DuckTerm.app — confirm returned false,
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
**Broke:** ⌘C/⌘V did nothing in DuckTerm.app even with a proper Edit menu.
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
