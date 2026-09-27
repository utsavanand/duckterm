# Bugs and backlog — single list

One place to see everything open, ordered by what to do next. The
[roadmap](roadmap.md) holds the reasoning, shipped history, and deferred
items with their triggers; this page is the working list.

Ownership means **asked and confirmed**, not "sent to". Where a session has
said it has not started something, that is recorded as such.

Last reconciled against `main` (`4876a2a`, installed **v0.4.73**) and open
PRs: 2026-09-27, by `product`. Every "verified" line below was checked
against code or the installed app that day.

## Waiting on the owner

These are blocked on a decision only the owner can make.

| Item | Decision needed |
| --- | --- |
| **F12 revert** (PR #96) | F12 work tracking shipped in v0.4.72 after the owner deferred it. Merge the revert, or keep F12. The Folder view spec below builds on F12. |
| **Folder view + Feature tracker** ([spec](folder-view-spec.md)) | Four open questions in the spec. Also: a Kanban runs into the F12 "heavyweight PM interface" concern. |
| **Tier 1 go-ahead for ui-dev** | ui-dev says B6, F6 and the scraper tests "await user direction"; it has not started them. |
| **Duck settle grace** | Shorten the 5-minute grace, or add a distinct "settling" pose. |
| **B2 connector setup design** | Review; connectors-dev is waiting. |
| **F7 plan hand-off preview** | Approve the preview; also: agent choice only, or a model field too? |
| **F11 scheduling** | The Stop-hook probe works; schedule the build. |
| **Oracle on WhatsApp** | Five open questions in [oracle-whatsapp-design.md](oracle-whatsapp-design.md). |

## In progress

| Item | Owner | State |
| --- | --- | --- |
| **B9** Terminal typing slows as sessions grow | main-dev | Fixed in PR #95 (p95 70.5 → 25.7 ms at 23 sessions); awaiting release and an installed Mac check |
| Full-window artifact view | ui-dev | Owner approved the preview; implementing on main |

## Tier 1 — do now (verified unbuilt, hours each)

| ID | Item | Owner | Verified 2026-09-27 |
| --- | --- | --- | --- |
| **B6** | Desktop notification setting does not persist; no feedback when the browser blocked notifications; already-waiting sessions notify in a burst at load and on enable | ui-dev (not started) | `notifyOn` is still plain `useState` from `Notification.permission` (App.tsx:182); no localStorage |
| **F6** | Highlight annotated spans in the Messages tab — owner's named priority | ui-dev (not started) | Messages.tsx only POSTs annotations; no highlight render path |
| — | Adversarial scraper unit tests (feed each `detect_state` its own trigger words in innocuous contexts) | ui-dev (not started) | `tests/unit/runtimes/` has no such test |

## Tier 2 — next

| ID | Item | Owner | Note |
| --- | --- | --- | --- |
| — | **Duck settle grace**: ducks look busy for 5 minutes after the agent finishes | main-dev | `IDLE_SETTLE_MS` is still `5 * 60_000` (sessions.ts:11). Needs the owner choice above. Also delays celebrations. |
| — | **Waiting lifecycle**: raised hands drop on the next agent event, not when the owner looks; `_reconcile_waiting` lets a screen scraper veto a real hook-driven `waiting` | main-dev | No fix on main. Owner expects hands to stay up until attended. |
| **B3** | Folder-rename / broadcast scope regression | main-dev | No fix commit on main. Reproduced twice; main-qa waiting to re-verify. |

## Tier 3 — larger, owner input needed

| ID | Item | Owner | State |
| --- | --- | --- | --- |
| **B2** | Connectors configured but not usable | connectors-dev | Design written; owner review pending |
| **F11** | Answer an agent from Oracle's chat without typing into its terminal | Oracle main-dev (`0048fef0`) | Probe done: free text works via the Stop hook; menus via the permission path. Open: 180 s vs 300 s timeouts. |
| **F7** | Plan hand-off | unassigned | Design merged ([plan-handoff-design.md](plan-handoff-design.md), PR #22); preview pending |
| **F8** | DuckCloud: setup flow, cost and idle shutdown, AWS | feature-remote-session | Remote work merged; design merged ([duckcloud-design.md](duckcloud-design.md)); setup flow not built |
| — | Folder view + Feature tracker | unassigned | Proposed ([folder-view-spec.md](folder-view-spec.md)) |
| — | Urgent inbox messages and an explicit Interrupt control | ui-dev | Designed; not built |
| — | Re-home the approvals UI | unassigned | Placement question only (TODO.md) |
| **F5** | Meta-harness vocabulary and composition | unassigned | Nomenclature → compatibility → model router, in that order |
| **F4** | Migrate a running session to another harness | unassigned | Seeded, not resumed |
| **F2** | Self-update to the latest DuckTerm release | unassigned | Not built |
| **F1** | Finish the DuckTerm rename in remaining prose | unassigned | Code-side names deliberately unchanged |

## Standing risks (not tickets, but they bite)

- **Releasing without re-reading the inbox.** F12 shipped in v0.4.72 and
  v0.4.73 after the owner had deferred it, because release-dev didn't
  re-read its inbox before merging. The DB is now schema v5. PR #96 records
  the lesson: re-read the inbox before merge, before tag and before install.
- **Backup sync mode has no exclusion test.** `gcloud storage rsync`
  inherits none of the tar path's credential/symlink filtering. Verified
  2026-09-27: `tests/unit/persistence/test_backup.py` covers only the tar
  path.
- **Flaky terminal browser tests.** `oracle-terminal-resize` and
  `side-panels` failed once in a local gate on 2026-09-26, then passed 6/6
  on repeat. Branch protection has no bypass, so a CI flake blocks every
  PR. (The connector heartbeat flake was fixed in PR #79.)
- **Untracked work.** Features keep reaching the roadmap only after they
  ship. The ask to every session: one line when a feature *starts*.

## Fixed recently (so nobody re-reports them)

- **B8** Artifact previews blank in the Mac app — v0.4.69 (PR #83). The Mac
  app's navigation filter refused the preview's `about:srcdoc` load.
- **B7** Context readout showed "0 left" for Opus 5 — v0.4.70 (PR #82).
- **B4** Missing header icon/favicons — verified fixed on installed v0.4.73:
  `favicon.svg` and `favicon.ico` ship in the package and serve real image
  types.
- **F9** Focus: pin up to three sessions side by side — v0.4.71 (PR #78).
- Inbox redesign — v0.4.68 (PR #80). Oracle nudge memory across restarts —
  v0.4.70 (PR #75). Nudges to act within remit — v0.4.71 (PR #77). Control
  tower menu questions open the terminal — v0.4.73 (PR #91).
- **B1** Messages panel showed the previous session's transcript — fixed
  `5a40863`.
- **B5** Opening Oracle corrupted terminal wrapping — fixed by layout in
  v0.4.51. **The underlying dropped-resize defect is still latent**:
  `settleOpening` early-returns before `fit`/`sendResize`. Start there if
  geometry breaks again.
