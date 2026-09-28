# Retro — lessons from real breakage

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
