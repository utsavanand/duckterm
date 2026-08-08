# RubberTerm release SOP

**Three tiers of one product. A fix is written once and flows downhill as it
earns trust — never rewritten per tier.**

```
branch ──► main ──► beta (pre-release wheel) ──► prod (final wheel)
 write      tests       a human tester            your daily driver
```

| Tier | Is | Runs as |
|---|---|---|
| **dev** | git source checkout | `DUCKTERM_INSTANCE=dev python -m duckterm.cli serve` |
| **beta** | wheel, version `0.4.0b1` | `pipx install <url>` → `DUCKTERM_INSTANCE=beta` |
| **prod** | wheel, version `0.4.0` | `pipx install <url>` → `DUCKTERM_INSTANCE=prod` |

`DUCKTERM_INSTANCE` isolates each tier's data dir, tmux socket, port, and hook
callback URL — the tiers never share a database or fight over sessions. Any
machine running a tier needs Python 3.11+, tmux, and the agent CLIs
(claude/codex/…) on PATH.

## The four steps

1. **Develop** — branch off `main`, write + test, run `scripts/check.sh`
   (ruff/black/mypy/pytest/vitest/playwright), merge. `main` is always shippable.
2. **Cut beta** — bump `__version__` in `src/duckterm/__init__.py` to a
   pre-release (`0.4.0b1`), commit, then `scripts/release.sh beta` → a GitHub
   pre-release with the wheel attached.
3. **Test beta** — a tester installs it by URL on their own machine:
   ```
   pipx install --suffix @beta <wheel-url>
   DUCKTERM_INSTANCE=beta duckterm@beta serve
   DUCKTERM_INSTANCE=beta duckterm@beta install-hooks
   ```
   Bugs → back to step 1, cut `b2`, `b3`… until it's solid.
4. **Promote** — bump `__version__` to the final version (`0.4.0`, drop the
   `b1`), commit, then `scripts/release.sh prod` → a full GitHub release. Install
   it as your daily driver:
   ```
   pipx install <prod-wheel-url>
   DUCKTERM_INSTANCE=prod duckterm serve
   ```

## Versioning

`__version__` is the single source of truth (pyproject reads it dynamically) —
one line to bump. PEP 440 suffixes mark pre-releases: `0.4.0b1` (beta),
`0.4.0rc1` (release candidate), `0.4.0` (final). The number tells the story:
`0.4.0b2` is the second beta of the 0.4.0 line.

## Guardrails (already built in)

- `scripts/release.sh` refuses a final version for `beta` or a pre-release for
  `prod`, so a beta can't accidentally ship as prod.
- Schema-version guard: if a beta changes the DB shape, an older prod refuses to
  open that data (`SchemaTooNewError`) rather than corrupting it. (The tiers keep
  separate homes anyway, so they don't touch the same DB.)
- Pidfile guard: an instance refuses to start if another live server owns its
  home.
- Rollback: prod is a pinned wheel, so rolling back is `pipx install` of the
  previous wheel; its data (its own home dir) is untouched.
- Backups: the real backup unit is the instance's home dir (`~/.duckterm-prod/`,
  which holds the SQLite DB). Snapshots are relaunch-pointers, not backups.

## Branch model

**Now (solo): tag betas off `main`.** One linear code line; a beta is a tag
(`v0.4.0b1`) at a commit, prod is a later tag (`v0.4.0`) on the same line — no
separate branch to keep in sync. `release.sh` works exactly this way.

**Upgrade trigger (when the second developer is active):** if two people are
shipping overlapping work and a beta needs to freeze while `main` keeps moving,
add a long-lived `beta` branch then. Not before — it's reversible, and the extra
merge bookkeeping isn't worth it for one or two devs on a linear flow.

## First cycle (from today's state)

`main` has none of the audit fixes yet — they're on `instance-isolation`. So:

1. Merge `instance-isolation` → `main` (brings in all the audit fixes).
2. `__version__ = 0.4.0b1`, `scripts/release.sh beta`.
3. Tester `pipx install`s it, runs `DUCKTERM_INSTANCE=beta`, validates.
4. Approved → `__version__ = 0.4.0`, `scripts/release.sh prod` → daily driver.
