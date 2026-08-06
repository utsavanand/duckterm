# Artifacts & releases: production / beta / dev

Three tiers of the same product. A fix is written **once** and flows through
them as it earns trust — it is never written three times.

## The three artifacts

| Tier | What it is | How you run it | Purpose |
|---|---|---|---|
| **Production** | A pinned installed wheel (a full release) | `pipx install` the prod wheel | Your daily driver. Never edited. |
| **Beta / staging** | An installed wheel of a **pre-release** version | `pipx install` the beta wheel | You + friends test the next version before it becomes prod. |
| **Dev** | The git source checkout | `python -m duckterm.cli serve` from a worktree | Where fixes are written and seen live. |

Prod and beta are both **pip installs** — real installed apps, just different
versions. Dev is **source**, because dev is where code changes; you can't
"install" a thing you're actively editing.

## How a fix reaches all three

```
write fix on a branch  →  merge to main  →  cut BETA release  →  promote to PROD release
      (dev/source)            (main)         (pre-release wheel)    (final wheel)
```

1. I write the fix on a branch and it's tested + merged to `main`.
2. Bump `__version__` to a pre-release (e.g. `0.4.0b1`) and run
   `scripts/release.sh beta` → a GitHub **pre-release** with the wheel attached.
   You + friends `pipx install` it and test.
3. When it's proven, bump `__version__` to the final version (e.g. `0.4.0`) and
   run `scripts/release.sh prod` → a GitHub **full release**. Install it as your
   daily driver.

The same change ends up in all three — by **flowing** dev → beta → prod, not by
editing three copies.

## Running them side by side, safely

Each tier is a separate RubberTerm **instance**. Set `DUCKTERM_INSTANCE` and the
data dir, tmux socket, port, and hook callback URL all derive from it — so the
three never share a database or fight over tmux panes (a shared socket used to
let one instance kill another's live agents; see docs/production-audit.md
finding B). A pidfile guard also refuses to start a second server on the same
home.

```
# Production (installed wheel)
DUCKTERM_INSTANCE=prod duckterm serve          # ~/.duckterm-prod, port derived from "prod"
DUCKTERM_INSTANCE=prod duckterm install-hooks

# Beta (installed wheel of a pre-release version, in its own pipx venv)
pipx install --suffix @beta ./duckterm-0.4.0b1-py3-none-any.whl   # -> duckterm@beta
DUCKTERM_INSTANCE=beta duckterm@beta serve     # ~/.duckterm-beta, its own port

# Dev (from source)
DUCKTERM_INSTANCE=dev python -m duckterm.cli serve   # ~/.duckterm-dev, its own port
```

`duckterm serve` with no `DUCKTERM_INSTANCE` is the legacy default
(`~/.duckterm`, port 4300) — unchanged.

## Versioning

`src/duckterm/__init__.py`'s `__version__` is the single source of truth;
`pyproject.toml` reads it dynamically, so a release bumps one line. Use PEP 440
suffixes for pre-releases: `0.4.0b1` (beta), `0.4.0rc1` (release candidate),
`0.4.0` (final). `scripts/release.sh` refuses a beta with a final version or a
prod with a pre-release version, so a beta can't accidentally ship as prod.

## Cutting a release

```
# from a clean checkout on the commit you want to ship:
#   edit __version__ to the target version, commit it, then:
scripts/release.sh beta     # or: prod
```

It builds the wheel (dashboard bundled), tags the commit, and creates the GitHub
release (beta = pre-release, prod = full) with the wheel + sdist attached and
install instructions in the notes.

## Backups (important for daily reliance)

Snapshots are relaunch-pointers, not backups. The real backup unit is the
instance's home dir — e.g. `~/.duckterm-prod/` — which holds the SQLite DB and
session data. Back that up if the sessions matter. (See docs/production-audit.md.)
