# Bugs and backlog — single list

One place to see everything open, ordered by what to do next. The
[roadmap](roadmap.md) holds the reasoning, shipped history, and deferred
items with their triggers; this page is the working list.

Ownership means **asked and confirmed**, not "sent to". Where a session has
stated it has not started something, that is recorded as such.

Last reconciled against `main`: 2026-09-26.

## Tier 1 — do now (verified unbuilt, hours each)

| ID | Item | Owner | Verified |
| --- | --- | --- | --- |
| **B6** | Desktop notification setting does not persist; no feedback when the browser has blocked notifications; already-waiting sessions notify in a burst at load and on enable | ui-dev | `grep` for localStorage/notify in App.tsx → 0 matches |
| **F6** | Highlight annotated spans in the Messages tab — owner's named priority | ui-dev | Messages.tsx does not even fetch annotations |
| — | Adversarial scraper unit tests (feed each `detect_state` its own trigger words in innocuous contexts) | ui-dev | no such test exists in `tests/unit/runtimes/` |

All three dispatched to `ui-dev` as one release, in this order.

## Tier 2 — next

| ID | Item | Owner | Note |
| --- | --- | --- | --- |
| — | **Duck settle grace**: ducks are drawn typing for 5 minutes after the agent finishes (`IDLE_SETTLE_MS`) | main-dev | Needs an owner choice: shorten the grace, or add a distinct "settling" pose. Also makes celebrations up to 5 min late. |
| — | **Waiting lifecycle**: raised hands drop on the next agent event, not when the owner looks; `_reconcile_waiting` lets a screen scraper veto a real hook-driven `waiting` | main-dev | Owner's expectation: hands stay up until they get attention |
| **B4** | Packaged header icon/favicons missing from the wheel; URLs return fallback HTML with HTTP 200 | ui-dev | Fix the packaging **validation**, not only the assets |
| **B3** | Folder-rename / broadcast scope regression — moving a recipient away cancels its broadcast, then renaming the old folder wrongly makes it readable in the new scope | main-dev | Reproduced twice; sits *under* broadcast and inbox delivery, so features keep stacking on it. `main-qa` idle waiting to re-verify. **main-dev confirms still queued.** |

## Tier 3 — larger, owner input needed

| ID | Item | Owner | Blocked on |
| --- | --- | --- | --- |
| **B2** | Connectors configured but not usable | connectors-dev | Owner review of the rewritten connector setup design |
| **F11** | Answer an agent from Oracle's chat without typing into its terminal | Oracle main-dev (`0048fef0`) | Owner scheduling; the Stop-hook probe blocks the rest of the design. **Open:** the 180 s hook poll cap is far shorter than a human answering a form. |
| **F9** | Focus — pinned sessions view | ui-dev (preview) | Owner preview approval |
| **F10** | Request status updates (Received / In progress / Done, chain-aware) | main-dev | Sequencing agreed: Received+Done need no new machinery, do them first |
| **F7** | Plan hand-off (plan with one agent, implement with another) | unassigned | Owner preview approval |
| **F8** | DuckCloud setup flow, cost + idle shutdown, AWS | feature-remote-session | Remote work merged; this is the onboarding layer |
| **F5** | Meta-harness vocabulary and composition | unassigned | Largest item; nomenclature → compatibility → model router, in that order |
| **F4** | Migrate a running session to another harness | unassigned | Must be honest: seeded, not resumed |
| **F2** | Self-update to the latest DuckTerm release (the Settings *surface* shipped in v0.4.45) | unassigned | — |
| **F1** | Finish the DuckTerm rename in remaining prose | unassigned | Code-side names deliberately unchanged |

## Standing risks (not tickets, but they bite)

- **Backup sync mode has no exclusion test.** `gcloud storage rsync`
  inherits none of the tar path's credential/symlink filtering, and that
  filtering is why the verified archive contained zero credential files.
  Asked `main-dev` four times; still unanswered. Treated as an open risk
  against a shipped feature.
- **Flaky required checks gate the whole merge queue.** Branch protection
  has no bypass, so one flaky test blocks every PR including docs-only
  ones. Two incidents in two days: `test_fork_chain_builds_lineage` (fixed
  in v0.4.54) and `test_connector_disable` asserting **exact equality of
  two nanosecond timestamps** taken ~91 ms apart — the documented RETRO
  pattern about wall-clock assertions. The second still needs a tolerance
  fix rather than luck.
- **Untracked work.** Two reconciliation passes found nine shipped,
  documented features with no roadmap entry (seven on 2026-09-25, plus
  Control Tower and message bookmarks). The ask to every session is one
  line when a feature *starts*.

## Fixed recently (so nobody re-reports them)

- **B1** Messages panel showed the previous session's transcript — fixed
  `5a40863`; missing React `key` meant one component instance was reused
  across switches.
- **B5** Opening Oracle corrupted terminal wrapping — fixed by layout in
  v0.4.51. **The underlying dropped-resize defect is still latent**:
  `settleOpening` early-returns before `fit`/`sendResize`, so a resize can
  still be dropped from grid splits, folder-grid toggles, tab switches, or
  window resize. Start there if geometry breaks again.
- Fork-race CI flake — fixed v0.4.54. Live sessions falsely showing
  Interrupted — fixed v0.4.53. Ordinary output flipping session status —
  fixed v0.4.59.
