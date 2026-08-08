# RubberTerm releases & testing

The one page to share with anyone who's installing or testing RubberTerm. It
says what's on each tier, how to install it, and what to test. Pair it with
[release-sop.md](release-sop.md) (how releases are cut).

<!-- CURRENT-VERSIONS:START (auto-updated by scripts/release.sh — do not edit by hand) -->
| Tier | Version | Install from |
|---|---|---|
| **prod** (stable, daily) | `v0.1.0` | https://github.com/utsavanand/duckterm/releases/tag/v0.1.0 |
| **beta** (staging, testing) | `v0.4.0b1` | https://github.com/utsavanand/duckterm/releases/tag/v0.4.0b1 |
| **dev** (source) | `main` | git checkout |
<!-- CURRENT-VERSIONS:END -->

## Install & run

Each tier runs as its own isolated instance (own data dir, port, tmux socket) —
they never collide. Any machine needs **Python 3.11+**, **tmux**, and the agent
CLIs you'll drive (`claude`, `codex`, …) on PATH.

### Beta (what testers install)

```sh
# 1. install the beta wheel into its own pipx app named `duckterm@beta`
pipx install --suffix @beta \
  https://github.com/utsavanand/duckterm/releases/download/v0.4.0b1/duckterm-0.4.0b1-py3-none-any.whl

# 2. run it as the beta instance (its own ~/.duckterm-beta, own port)
DUCKTERM_INSTANCE=beta duckterm@beta serve
# open the URL it prints (a per-instance port, not 4300)
```

**Hooks are optional** and only needed to test live state/approvals (see below):

```sh
# ONLY if testing approvals/state, and ONLY if OK touching ~/.claude:
DUCKTERM_INSTANCE=beta duckterm@beta install-hooks
```

> ⚠️ `install-hooks` writes to `~/.claude/settings.json`, which is **shared
> across every RubberTerm/Claude Code setup on the machine** — it is NOT isolated
> per instance. On a fresh/dedicated test machine it's harmless. On your daily
> machine, skip it unless you specifically want to exercise approvals, since it
> can route your agent's events to the beta instance.

### Prod (daily driver)

```sh
pipx install \
  https://github.com/utsavanand/duckterm/releases/download/v0.1.0/duckterm-0.1.0-py3-none-any.whl
DUCKTERM_INSTANCE=prod duckterm serve
DUCKTERM_INSTANCE=prod duckterm install-hooks   # your daily setup; fine here
```

### Dev (source)

```sh
git clone https://github.com/utsavanand/duckterm && cd duckterm
pip install -e ".[dev]" && (cd web && npm install)
DUCKTERM_INSTANCE=dev python -m duckterm.cli serve
```

### Uninstall / roll back a tier

```sh
pipx uninstall duckterm@beta          # remove beta; data in ~/.duckterm-beta stays
pipx install <older-wheel-url>         # roll prod back to a previous wheel
```

## What to test (beta checklist)

Copy this into your report; note pass/fail + notes per item.

**Core daily use**
- [ ] Launch a session (New session → pick a folder → launch); it appears and shows live output.
- [ ] Type into the terminal; keystrokes reach the agent and echo back.
- [ ] Create a folder; nest a subfolder; drag a session into it.
- [ ] Open a folder as a grid; split/resize panes.
- [ ] Fork a session; the fork lands in the parent's folder.
- [ ] Checkpoint a session; the checkpoint appears with a summary.

**The two Critical fixes this beta is really about**
- [ ] **Reboot honesty:** launch a session, reboot the machine, reopen the
      dashboard → the session shows **stopped/resumable**, NOT a stuck "busy".
- [ ] **Resume:** resume that session → for Claude Code it continues the
      conversation; for other agents the response says the conversation was NOT
      carried (starts fresh) rather than pretending it resumed.
- [ ] **Isolation:** running the beta doesn't disturb any other RubberTerm on
      the machine (separate port/home/socket); starting a second server on the
      same home is refused with a clear message.

**Cleanliness (the High fixes)**
- [ ] Launch with a typo'd command → it fails with a clear error and leaves NO
      orphaned git worktree/branch behind.
- [ ] Delete a session → its checkpoint files are gone from disk too.
- [ ] Resume/restore a session whose working dir was removed → refused with a
      clear "working directory no longer exists" message (not launched into $HOME).

**Report bugs:** open a GitHub issue tagged `beta` with the version (`v0.4.0b1`),
what you did, what you expected, what happened, and any terminal output.

## How this page stays current

The version table at the top is stamped automatically by `scripts/release.sh`
each time a beta or prod release is cut — don't edit it by hand. The install
steps and checklist are hand-maintained; update them when the flow or the
features change.
