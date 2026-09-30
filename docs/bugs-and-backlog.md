# Bugs and backlog — single list

One place to see everything open, ordered by what to do next. The
[roadmap](roadmap.md) holds the reasoning, shipped history, and deferred
items with their triggers; this page is the working list.

Ownership means **asked and confirmed**, not "sent to". Where a session has
said it has not started something, that is recorded as such.

Status last updated at the **v0.4.91** release (2026-09-29) by `release-dev`; items
below were last reconciled against code by `product` on 2026-09-27. Every "verified" line below was checked
against code or the installed app that day.

## Waiting on the owner

These are blocked on a decision only the owner can make.

| Item | Decision needed |
| --- | --- |
| **Folder view + Feature tracker** ([spec](folder-view-spec.md)) | F12 was reverted in v0.4.74, so the tracker no longer has work items to build on. Decide: a lighter tracker with its own minimal data, or wait for the F12 redesign. Plus four open questions in the spec. |
| **F12 redesign** | Reverted as too heavy. The lighter candidate on record: a derived "replied but nothing shipped" signal, with no states to maintain by hand. |
| **Tier 1 go-ahead for ui-dev** | ui-dev says B6, F6 and the scraper tests "await user direction"; it has not started them. |
| **Duck settle grace** | Shorten the 5-minute grace, or add a distinct "settling" pose. |
| **B2 connector setup design** | Review; connectors-dev is waiting. |
| **F7 plan hand-off preview** | Approve the preview; also: agent choice only, or a model field too? |
| **F11 scheduling** | The Stop-hook probe works and the path now carries inbox reminders; schedule relaying owner answers. |
| **Oracle on WhatsApp** | Five open questions in [oracle-whatsapp-design.md](oracle-whatsapp-design.md). |

## In progress

| Item | Owner | State |
| --- | --- | --- |
| **B9** native confirmation | main-dev | Shipped in v0.4.78; numbers are from Chromium. The owner typing across ~23 sessions in the Mac app settles it (PR #112). |

## Tier 1 — do now (verified unbuilt, hours each)

| ID | Item | Owner | Verified 2026-09-27 |
| --- | --- | --- | --- |
| **B6** | Desktop notification setting does not persist; no feedback when the browser blocked notifications; already-waiting sessions notify in a burst at load and on enable | ui-dev (not started) | `notifyOn` is still plain `useState` (App.tsx:201); no localStorage |
| **F6** | Highlight annotated spans in the Messages tab — owner's named priority | ui-dev (not started) | Messages.tsx only POSTs annotations; no highlight render path |
| — | Adversarial scraper unit tests (feed each `detect_state` its own trigger words in innocuous contexts) | ui-dev (not started) | `tests/unit/runtimes/` has no such test |

## Tier 2 — next

| ID | Item | Owner | Note |
| --- | --- | --- | --- |
| — | **Duck settle grace**: ducks look busy for 5 minutes after the agent finishes | main-dev | `IDLE_SETTLE_MS` is still `5 * 60_000` (sessions.ts:11). Needs the owner choice above. Also delays celebrations. |
| — | **Waiting lifecycle**: raised hands drop on the next agent event, not when the owner looks; `_reconcile_waiting` lets a screen scraper veto a real hook-driven `waiting` | main-dev | No fix on main. Owner expects hands to stay up until attended. |
| **B12** | **Messages tab and panel collapse** (owner-reported 2026-09-27): (a) collapsing or expanding the right panel or the Messages folder, the terminal and Messages don't re-fit cleanly; (b) tables in Messages are unreadable on a large MacBook screen | unassigned | (a) Likely the latent B5 defect: `settleOpening` (Terminal.tsx:134) returns before `fit`/`sendResize` when the pane is momentarily unmeasurable, with no retry. (b) `.rd-msg-text table` is `width: 100%` with no horizontal scroll wrapper, and `.rd-message` sets `overflow-wrap: anywhere`, so wide tables get squeezed and words split mid-character. Fix direction: retry the fit on the next frame; wrap tables in a horizontally scrolling container and stop breaking words inside cells; render terminal-style ASCII/box-drawing tables as monospace blocks. Needs a screenshot of the table the owner saw. |
| **B3** | Folder-rename / broadcast scope regression | main-dev | No fix commit on main. Reproduced twice; main-qa waiting to re-verify. |

## Tier 3 — larger, owner input needed

| ID | Item | Owner | State |
| --- | --- | --- | --- |
| **B2** | Connectors configured but not usable | connectors-dev | Design written; owner review pending |
| **F11** | Answer an agent from Oracle's chat without typing into its terminal | Oracle main-dev (`0048fef0`) | Stop-hook path now delivers inbox reminders (`cd25224`); relaying owner answers not built. Open: 180 s vs 300 s timeouts. |
| **F7** | Plan hand-off | unassigned | Design merged ([plan-handoff-design.md](plan-handoff-design.md), PR #22); preview pending |
| **F8** | DuckCloud: setup flow, cost and idle shutdown, AWS | feature-remote-session | Remote work merged; design merged ([duckcloud-design.md](duckcloud-design.md)); setup flow not built |
| — | Folder view + Feature tracker | unassigned | Proposed ([folder-view-spec.md](folder-view-spec.md)); needs rework after the F12 revert |
| — | Session controls: right-panel actions, Restart, Change model, Switch harness (includes F4) | architect (design requested) | Proposed ([session-controls-spec.md](session-controls-spec.md)) |
| — | Analytics page: tokens by day/model/agent/session and Agent Mail history | architect (design requested) | Owner-approved spec ([analytics-spec.md](analytics-spec.md)); mail daily rollup first so history accumulates |
| **F14** | Cross-host session discovery and messaging | unassigned | Designed ([cross-host-collaboration-design.md](cross-host-collaboration-design.md), PR #110) |
| **F10** | Request status updates (received / in progress / done, chain-aware) | unassigned | Removed with the F12 revert; not on main |
| — | Urgent inbox messages and an explicit Interrupt control | ui-dev | Designed; not built |
| — | Re-home the approvals UI | unassigned | Placement question only (TODO.md) |
| **F5** | Meta-harness vocabulary and composition | unassigned | Nomenclature → compatibility → model router, in that order |
| **F4** | Migrate a running session to another harness | unassigned | Seeded, not resumed |
| **F2** | Self-update to the latest DuckTerm release | unassigned | Not built |
| **F1** | Finish the DuckTerm rename in remaining prose | unassigned | Code-side names deliberately unchanged |

## Standing risks (not tickets, but they bite)

- **Releasing without re-reading the inbox.** F12 shipped in v0.4.72 and
  v0.4.73 after the owner had deferred it. Reverted in v0.4.74; the DB stays
  schema v5 with unused work tables. RETRO: re-read the inbox before merge,
  before tag and before install.
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

- **F12 work tracking reverted** — v0.4.74 (PR #96), per the owner's deferral.
- **Full-window artifact view** — v0.4.75 (PR #98).
- **B9** Terminal typing stays fast as sessions grow — v0.4.78 (PR #95);
  p95 at 23 sessions 70.5 → 25.7 ms in Chromium.
- Local and remote sessions in one window — v0.4.75 (PR #68); location
  icons v0.4.76-0.4.77; folders stay collapsed after reload v0.4.77 (PR
  #104); remote clouds on the Oracle page v0.4.79 (PR #111).
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
