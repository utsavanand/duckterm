# Review: local and remote sessions in one window

Date: 2026-09-26. Reviewed installed v0.4.64 and the corresponding source.
Status: correction implemented on `unified-session-window`; native acceptance passed.
PR review and release are pending.

## Conclusion

The implementation uses one dashboard connected to one selected machine. The
owner expects one dashboard containing sessions from every connected machine.
Changing the New Session form did not change that underlying architecture.
The feature is therefore not complete for the requested workflow, despite the
earlier release gates passing.

## Evidence and remaining uncertainty

| Observation | Finding |
| --- | --- |
| Local sessions disappeared after remote launch | `LaunchModal.tsx` calls `selectLaunchTarget(target, {})` after a cross-host launch. `AppDelegate.switchHost` replaces the dashboard connection; `DashboardWindow.connect` replaces its WKWebView. Local rows disappear because the page now reads the remote database. |
| Local agents appeared killed | Read-only inspection found 23 live local tmux panes and a healthy local dashboard on port 4300. Host switching does not call `ServerProcess.stop`. This supports a visibility failure; it cannot prove every previously running process survived without a pre-incident baseline. |
| Three extra DuckTerm sessions | The local database contains three recent, ungrouped, terminated Claude launch attempts in the DuckTerm checkout. Their Start/End intervals were 4, 4, and 28 ms. They likely explain the three extra entries; they are not evidence of three app instances. |
| Local launch failed | These attempts have no pane logs. The failure handler emits SessionEnd without the exception and removes the supervisor/worktree, but retains the session row. The historical error is not recoverable from these events. Current server PATH resolves both tmux and Claude, and the requested local folder exists; a missing PATH must not be presented as the established cause. |
| Remote project launch | The remote database records a session in `/home/duckterm/projects/sotto`, without a sidebar group. The code skips group assignment and `onCreated` on cross-host launches, then switches dashboards without passing the newly launched session key. |
| Another window appeared | The code reuses one DashboardWindow while replacing its web view. Inspection found one DuckTerm application process; window metadata does not establish what caused the reported additional window. External browser navigation is a separate path. Native reproduction remains required. |

The three failed local session keys are:

- `555d75cd9f0b4fe598341ef36398f27b`
- `1634ce5cab6f4e4792b3995bf2729da2`
- `1136e7e74dab4f9b8b3165a92db42a7c`

No sessions, project directories, app windows, or processes were removed or
restarted during this review. Existing remote Claude work was preserved.

## Why the tests missed it

`web/e2e/remote-launch.spec.ts` checks that choosing a destination leaves the form
mounted before launch. It does not exercise the successful native launch and
subsequent dashboard replacement. Browser mocks cannot establish native window
behavior. The list feed (`useEventStream.ts`), terminal WebSocket (`Terminal.tsx`),
and general API (`api.ts`) all address the current origin and use unqualified
session keys. Simply merging two lists, or deleting the host-switch call, would
leave actions misrouted or remote sessions invisible.

## Proposed interaction, for preview before implementation

One stable window keeps the existing sidebar folders and adds a machine label
to each session. A selected remote session does not change the scope of the app.

```text
DuckTerm                                             New session

Duckterm
  architect              This Mac          Running
  feature-remote-session This Mac          Running
Sotto
  API work               duckterm-dev      Running

Selecting API work opens its remote terminal in the same central pane.
The local rows remain present and their processes continue running.

New session
  Run on:          [This Mac / duckterm-dev]
  Sidebar folder:  [Choose folder]
  Project source:  [Existing folder / Clone Git / Copy local project*]
  Project folder:  [Browse… / New folder…]

  *Copy local project is available for remote destinations.
```

The checkout folder and sidebar folder remain separate concepts. Launching
adds and selects exactly one row in the chosen sidebar folder. It does not
navigate the dashboard or open another window. The Run on control remains
available for both local and remote launches regardless of the selected row.
A disconnected remote machine keeps its rows visible with a connection status;
local actions continue to work.

## Architecture required

1. Keep the desktop dashboard anchored to the local application. Maintain
   connections to configured remote hosts without changing the page origin.
   Direct remote dashboards may remain usable independently, but a desktop
   launch must never navigate into them.
2. Introduce a stable `(host_id, session_key)` reference for selection, caches,
   events, notifications, history, terminal input/output, and every mutation.
   Host IDs must survive SSH port changes and display-name changes. Existing
   local references migrate to the local host without changing local session IDs.
3. Provide one authenticated routing boundary for remote HTTP requests and
   streaming connections. A local gateway is the preferred design candidate:
   native code owns SSH tunnel lifecycle, and registers configured connections
   with the local server. Before implementation, review endpoint registration,
   authentication, origin checks, route allowlists, stream cancellation and
   size limits. Do not accept arbitrary upstream URLs or copy remote credentials
   into browser state. The Python backend remains stdlib-only.
4. Aggregate host feeds independently. Reconnect, replay and removal affect only
   that host. Selecting a remote row must not recreate the entire dashboard or
   discard local terminal state, drafts, or pending approvals.
5. Define sidebar membership across hosts explicitly. A desktop presentation
   folder must not silently grant cross-host inbox access: existing collaboration
   permissions are enforced by each destination server. Same-named folders on
   separate machines cannot be assumed to have the same identity or access.
   Preserve existing local folders and provide cross-host visual grouping without
   changing capability scope as a side effect.
6. Make launch failure and retry explicit: retain a useful error, avoid ordinary
   session rows for failed spawns, and reconcile retries after lost responses
   using a stable operation identity. Do not erase an agent that did start just
   because the response or subsequent group assignment failed.

## Delivery and acceptance

Send this review and preview through release-dev before proposing implementation
PRs. Release-dev coordinates the PR, independent QA on its exact head, and the
release version. No version bump is proposed by this review.

The release acceptance must exercise the actual native app, including:

- Start with local agents and a visible local terminal. Launch one remote agent;
  verify local pane PIDs, rows and terminal state remain, and one remote row
  appears in the chosen folder in the same window.
- Select the remote session, then launch a local agent successfully.
- Verify terminal input, stop/resume, rename, history, inbox and deletion address
  the selected host even when two hosts have the same session key.
- Disconnect and reconnect the remote host; local work remains usable, remote
  rows recover without duplication, and no new window opens.
- Fail a local spawn, interrupt a launch response, and retry. Keep actionable
  errors without dead-row clutter or duplicate live agents.
- Relaunch the app and verify mixed-host selection and grouping persist.
- Use `test:true` fixtures and clean them up. Do not use or terminate existing
  user agents as test fixtures.

Before any cleanup of the existing three failed rows, preserve their diagnostic
evidence and confirm they are failed attempts rather than valuable session
history. Such cleanup cannot substitute for the architecture correction.


## Implementation decision and evidence

The implementation uses the existing native WebKit bridge instead of a local
Python gateway. One local WKWebView remains mounted. Its native controller owns
SSH tunnels, authenticated finite HTTP requests, and terminal WebSockets. No
additional listening port, proxy route, remote WebView, or cross-origin browser
credential store is introduced. Native request paths are allowlisted, redirects
are refused, response collection is bounded, and remote credentials remain in
native code. HTTP and long-lived WebSockets have separate connection pools.

Local session references remain unchanged; remote references encode both the
SSH target and the server session key. Session actions, messages, history,
artifacts, project files, rules, connectors and meta-harness operations route to
that reference's host. Remote snapshots update independently of the local SSE
feed. Native notifications and approval/inbox indicators include remote hosts.
Sidebar assignments for remote rows persist in the local dashboard's storage;
this presentation metadata does not grant cross-machine inbox capabilities.
Folder-level broadcasts/interactions remain scoped to This Mac and are labeled
accordingly; each remote session’s own Inbox is routed to its remote server.

The actual native test caught two gaps after unit/browser checks: newly created
rows were hidden in collapsed destination folders, and a quiet terminal waited
for its first output before accepting input. New launches now reveal their
folder, and native WebSocket handshake completion enables input independently
of output. Neither correction reloads the dashboard.

Native acceptance uses the actual AppDelegate, DashboardWindow and WebKit with a
separate bundle identifier, local DB, and tmux socket, plus the isolated remote
QA service. All fixture launches use `test:true`; their agents are removed after
checking. The successful run verified one unchanged window/web view, retained
local terminal DOM, remote terminal input/output, local launch while a remote
row is selected, actionable missing-command errors without extra sidebar rows,
local launch during a remote tunnel outage, and recovery without duplicate rows.
It does not kill or use the user's working agents as fixtures.

Reproduce after building `web/dist`:

```sh
python3 scripts/test_unified_sessions_native.py --remote-host duckterm-dev --remote-port 4341
```

Native screenshot: `/tmp/duckterm-unified-native.png`. Gate and native logs:
`/tmp/duckterm-unified-gate.log` and `/tmp/duckterm-unified-native.log`.
The test's remote host is explicit, not an automatic CI default.

Launch validation now rejects a missing project folder or command before
publishing SessionStart. Unexpected spawn failures retain their exception in an
archived diagnostic event and remain out of the live sidebar. Existing failed
user rows are left untouched. General reconciliation of a lost normal-launch
response remains a separate concern; requests are never automatically retried.
