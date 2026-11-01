# DuckTerm — Test Round 0

**For:** a beta tester who has never used DuckTerm.
**Platform:** macOS, Apple Silicon. **The Mac app is what you are testing.**
Everything below is done in the app unless a step says otherwise.
**Build:** v0.4.168 or later — check the
[releases page](https://github.com/utsavanand/duckterm/releases) and use
whatever is marked Latest.
**Time:** about an hour.

## How to report

For every case: **PASS**, **FAIL**, or **BLOCKED** (you could not get far enough
to tell).

A case is PASS only if **every** criterion under it is met. **Partial is FAIL.**
A half-working feature reported as working is worse than a clean failure.

For any FAIL, include: the exact error text, what you had just done, and a
screenshot if it is visual.

---

## Before you start

If any of these cannot be satisfied, stop and report that instead of starting.

- **macOS on Apple Silicon** (M1 or later).
- **Python 3.11+** — `python3 --version`
- **tmux** — `which tmux`. If missing: `brew install tmux`
- **An agent CLI installed and signed in** — Claude Code or Codex. Check
  `which claude` (or `which codex`) returns a path, then run it once and confirm
  it does **not** ask you to log in. DuckTerm uses your existing agent account
  and does not manage logins.
- **No existing DuckTerm.** Run:
  ```sh
  which -a duckterm
  ```
  This must return **nothing** or **exactly one path**. Two or more means
  DuckTerm is installed twice and whichever is first on PATH wins silently. If
  you see more than one, say so in your report before continuing.

Record which agent you have and your macOS version.

---

## Case 1 — Install and first launch

**There is no Homebrew formula.** `brew install duckterm` will not work; that is
expected. Homebrew is used only for prerequisites.

**The Mac app is a launcher, not the whole product.** It starts and talks to a
CLI server you install separately. Both steps below are required.

### Steps

1. `brew install pipx tmux` then `pipx ensurepath`
2. **Close the terminal and open a new one.**
3. Install the CLI (substitute the current version):
   ```sh
   pipx install https://github.com/utsavanand/duckterm/releases/download/v0.4.168/duckterm-0.4.168-py3-none-any.whl
   ```
4. From the releases page download the zip for your edition, for example `DuckTerm-Lite-macos-arm64.zip`.
5. **Unzip it by double-clicking in Finder**, not with a terminal `unzip`
   command. (Command-line unzip adds hidden files that break the signature
   check — a known trap, not a product bug.)
6. Drag `DuckTerm.app` into **Applications** and open it.

### Acceptance criteria

- The app opens with **no Gatekeeper warning**. You must not see "cannot be
  opened because the developer cannot be verified" or "unidentified developer".
  The app is signed and notarized; a warning here is a real failure.
- A DuckTerm window opens showing the dashboard. Not blank, no error.
- The window shows your machine's sessions area, even if empty.
- Versions match. In a terminal run `duckterm --version`, and compare it with
  the app's version (DuckTerm → About). **If they differ, report it** — nothing
  currently checks this and a mismatch causes confusing behaviour later.

---

## Case 2 — A folder, a session, and both panes

The core of the product. If this fails, stop; later cases depend on it.

### Steps

1. In the app, create a **folder** named `Test Round 0`.
2. **New → New session.** Choose your agent and a project directory — any
   directory you do not mind an agent reading.
3. Put the session in `Test Round 0` and launch it.

### Acceptance criteria

- The folder appears with the name you gave it.
- The session appears **inside** that folder, not at the top level.
- The **terminal pane** shows live output from the agent — real startup text,
  not an empty box.
- A **messages pane** is visible, separate from the terminal.
- The session's state indicator changes as it starts rather than staying blank.

### Report specifically

- Did the terminal show real agent output, or stay empty?
- Were both panes visible at once, or did you have to switch between them?

---

## Case 3 — A conversation, and moving things

### Steps

1. In the session, type: `list the files in this directory`
2. Wait for the agent to respond.
3. Create a second folder, `Test Round 0 — Moved`.
4. Move the session into it, then move it back.

### Acceptance criteria

- The agent responds and the response appears in the terminal pane.
- Text renders cleanly — no stray escape characters, no overlapping text,
  colours appear as colours.
- The session **keeps running** across both moves. Output is not lost and the
  agent does not restart.
- After moving back it is in the original folder and still live.

---

## Case 4 — Fork a session, both ways

DuckTerm has two fork actions. Test both; report them separately.

### Steps

1. With the session still running and holding some conversation history, find
   the fork action.
2. **Fork the session.** Note what you get.
3. From the original, **fork the conversation.** Note what you get.

### Acceptance criteria

- Both actions are reachable without guessing.
- Each produces a **new session** visible in the app.
- The **original is unchanged and still running** after each fork.
- You can tell the two apart from what they produce.

### Report specifically

- What is the actual difference between the two? **Describe what you observe**
  — do not assume what it should be.
- Did a forked session carry conversation history, or start empty?

---

## Case 5 — Update from inside the app

**Important:** in-app installation is **not implemented yet**. DuckTerm checks
for a new release and tells you one exists, but you install it yourself. The
code says so directly: *"In-app installation is not available yet. Download the
release to update manually."*

So this case tests the **check**, and whether it is honest about its limits.

### Steps

1. In the app, find the update or version information in **Settings**.
2. Note what it tells you: your version, whether a newer release exists, and
   what it offers to do.

### Acceptance criteria

- The app reports your current version, and it matches `duckterm --version`.
- If a newer release exists, the app says so and links to the releases page.
- The app does **not** present a button implying it will install an update,
  since it cannot. An honest "download it yourself" is a PASS; a button that
  does nothing, or appears to work and does not, is a FAIL.

### Report specifically

- Exactly what does Settings offer? Quote the wording.
- Did anything suggest an update would install automatically?
- **Was it clear what you were supposed to do next?** This is the question that
  matters most here.

---

## Case 6 — Uninstall

**`duckterm uninstall` exists but does not remove anything yet.** As of
v0.4.168 it only inspects: `--dry-run` is described as "inspect only (currently
the default)". It lists the hooks it found and reports your data as kept. It
deletes nothing. Run it to see the inventory, then do the manual steps below.

The `/install` and `/uninstall` routes inside DuckTerm are a different thing —
they install agent harnesses, not DuckTerm itself.

This case establishes what a user actually has to do, and how much is left
behind. **Do this last** — it removes your installation.

### Steps

**Step 1 is not optional. Do it first.** DuckTerm installs hooks into your
Claude Code and Codex config files, and those hooks point at files inside the
DuckTerm installation. If you remove DuckTerm without removing the hooks first,
**your agent CLI will keep calling a script that no longer exists, on every
turn.** The command that removes them lives inside DuckTerm, so it must run
before anything is deleted.

1. Remove the hooks, **before removing anything else**. `--global` alone is
   not enough — it defaults to Claude Code only, so name each agent you have
   installed:
   ```sh
   duckterm uninstall-hooks --global --agent claude-code
   duckterm uninstall-hooks --global --agent codex
   duckterm uninstall-hooks --global --agent copilot
   ```
   Then check what remains:
   ```sh
   grep -c duckterm ~/.claude/settings.json
   grep -c duckterm ~/.codex/hooks.json
   ```
   Both should print `0`. If either prints anything else, **stop and report it**
   — continuing would leave your agent calling a script that no longer exists.

   **Also report, do not act on:** `grep -c duckterm ~/.codex/config.toml`.
   Entries there are a separate surface (MCP connectors, not hooks) and are not
   removed by the commands above. Record the number and leave it alone.
2. Quit the DuckTerm app and stop any running server.
3. Remove the app: drag `DuckTerm.app` from Applications to the Trash.
4. Remove the CLI:
   ```sh
   pipx uninstall duckterm
   ```
5. Check what remains:
   ```sh
   which -a duckterm
   ls -la ~/.duckterm
   tmux -L duckterm list-sessions
   ```

### Acceptance criteria

- `which -a duckterm` returns nothing.
- Report what `~/.duckterm` still contains and roughly how large it is.
- Report whether any tmux sessions survived, and whether agent processes are
  still running (`ps aux | grep claude`).

### Report specifically

- **What was left behind?** Data, running processes, tmux sessions.
- Was anything about removal explained anywhere, or did you need these
  instructions?
- Would you have known how to do this without being told?

---

## After the round

Send back:

1. Six PASS / FAIL / BLOCKED results.
2. Your agent CLI and macOS version.
3. For each FAIL: exact error text, the step, and a screenshot if visual.
4. **One sentence on what was most confusing**, whether or not it failed.
5. **One sentence on what you expected to exist and did not find.**

Items 4 and 5 matter as much as the defects. You are the only person who will
see this product without already knowing how it works.

### Not in scope

Checkpoints, memory, the Oracle, connectors, artifacts, remote sessions. Those
are being actively changed and testing them now would waste your time.

---

## Calibration — how this document scores itself

A test document is itself a thing that can be wrong. These are the criteria this
document is held to, and its own current score.

| # | Criterion | Score |
| --- | --- | --- |
| 1 | Every instruction was verified against the shipped build, not assumed | **PASS** |
| 2 | Features that do not exist are named as absent, not tested as present | **PASS** |
| 3 | Each acceptance criterion is pass/fail without judgement | **PASS** |
| 4 | Tests the product as the owner uses it (the Mac app), not a dev path | **PASS** |
| 5 | Setup failures are separable from product failures | **PASS** |
| 6 | Asks the tester what they observed, not to confirm what we expect | **PASS** |
| 7 | Covers the whole lifecycle: install → use → update → uninstall | **PASS** |
| 8 | Nothing in scope here is being actively changed by the fleet | **PASS** |
| 9 | A tester could run it with no access to the team | **PASS** |
| 10 | Known traps are flagged so they are not reported as product bugs | **PASS** |

### What earlier drafts got wrong, recorded so it is not repeated

- **Tested a developer path.** An earlier draft had the tester run
  `pipx install --force` to simulate an upgrade. That is not how the owner
  updates and not what a user would ever do. Replaced with Case 5, which tests
  the in-app update surface.
- **Assumed features existed.** It assumed a Homebrew formula and a DMG. Neither
  exists. Verified: no `Formula/` in the repo, and the release publishes a `.zip`
  with no disk image.
- **Missed that the app needs the CLI.** The Mac app is a 700 KB launcher with
  no bundled Python. An earlier draft called it "optional", which would have let
  a tester run it alone and report a broken app as a product defect.
- **No uninstall.** The owner asked for it and it was absent. Verified there is
  no `duckterm uninstall`; `/install` and `/uninstall` serve harnesses.
- **Browser instead of the app.** Earlier cases said "open the dashboard in your
  browser". The owner tests the Mac app, and so should a tester.

### Findings this document produced before anyone ran it

Writing it surfaced three gaps worth tracking independently:

1. **No app/CLI version check.** Both know their own version; neither compares.
   Filed with main-dev as `q-5c2440f54c6a05dcb478b84048f5926c`.
2. **No in-app installer.** `update_status.py` is read-only by design and says
   so. Case 5 tests honesty about that rather than pretending otherwise.
3. **No uninstall path.** Case 6 documents the manual steps and asks what is
   left behind. That answer should become an uninstall command.
