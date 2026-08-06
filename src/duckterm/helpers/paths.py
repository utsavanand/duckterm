"""Storage locations under ~/.duckterm/ (or ~/.duckterm-<instance>/).

The root is resolved by helpers.instance.home(): explicit DUCKTERM_HOME wins,
else it derives from DUCKTERM_INSTANCE, else the legacy ~/.duckterm."""

from pathlib import Path

from duckterm.helpers import instance


def home() -> Path:
    return instance.home()


def db_path() -> Path:
    return home() / "db.sqlite"


def worktrees_dir() -> Path:
    return home() / "worktrees"


def snapshots_dir() -> Path:
    return home() / "snapshots"
