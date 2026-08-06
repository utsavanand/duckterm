"""One instance id isolates a whole RubberTerm instance.

Before this, four independent env vars (DUCKTERM_HOME, DUCKTERM_TMUX_SOCKET,
DUCKTERM_PORT, DUCKTERM_URL) each isolated one facet, and getting any one wrong
let two instances silently corrupt or hijack each other's work — the tmux socket
especially, since a second server's reconcile() adopts every pane on a shared
socket and can kill the first server's live agents.

DUCKTERM_INSTANCE fixes that: set it once (e.g. "prod" / "dev" / "beta") and the
home dir, tmux socket, port, and callback URL all derive from it, so one setting
isolates everything. An explicit per-facet env var still wins where set (tests
and the e2e harness rely on that), so this is additive and backward-compatible:
with no DUCKTERM_INSTANCE and no overrides, behaviour is exactly as before.
"""

import hashlib
import os
import re
from pathlib import Path

_DEFAULT_PORT = 4300
# Ports for derived instances live above the default so they don't collide with
# a plain (no-instance) server on 4300. Range chosen to stay well clear of it.
_PORT_BASE = 4310
_PORT_SPAN = 600  # 4310–4909, hashed per instance id


def instance_id() -> str | None:
    """The instance id, or None for a plain (legacy) server. Constrained to a
    filesystem/tmux-safe token so it can't escape into a path or socket name."""
    raw = os.environ.get("DUCKTERM_INSTANCE", "").strip()
    if not raw:
        return None
    if not re.fullmatch(r"[A-Za-z0-9._-]{1,32}", raw):
        raise ValueError(f"invalid DUCKTERM_INSTANCE {raw!r}: use 1-32 chars of [A-Za-z0-9._-]")
    return raw


def home() -> Path:
    """Storage root. Explicit DUCKTERM_HOME wins; else derive from the instance
    id; else the legacy ~/.duckterm."""
    override = os.environ.get("DUCKTERM_HOME")
    if override:
        return Path(override)
    iid = instance_id()
    return Path.home() / (f".duckterm-{iid}" if iid else ".duckterm")


def tmux_socket() -> str:
    """tmux socket namespace. Explicit DUCKTERM_TMUX_SOCKET wins (tests/e2e);
    else derive from the instance id so a per-instance socket keeps reconcile()
    from ever seeing another instance's panes."""
    override = os.environ.get("DUCKTERM_TMUX_SOCKET")
    if override:
        return override
    iid = instance_id()
    return f"duckterm-{iid}" if iid else "duckterm"


def port() -> int:
    """The server port. Explicit DUCKTERM_PORT wins; else a stable per-instance
    port derived by hashing the id into a fixed range; else 4300."""
    override = os.environ.get("DUCKTERM_PORT")
    if override:
        return int(override)
    iid = instance_id()
    if not iid:
        return _DEFAULT_PORT
    digest = hashlib.sha256(iid.encode()).digest()
    return _PORT_BASE + int.from_bytes(digest[:4], "big") % _PORT_SPAN


def server_url() -> str:
    """The base URL agents' hooks call back on (heartbeats, event ingest,
    approvals). Explicit DUCKTERM_URL wins; else this instance's own host:port —
    so a dev-launched agent never reports into prod."""
    override = os.environ.get("DUCKTERM_URL")
    if override:
        return override.rstrip("/")
    return f"http://127.0.0.1:{port()}"
