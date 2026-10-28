"""One instance id isolates a whole DuckTerm instance.

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
from typing import TypedDict

_DEFAULT_PORT = 4300
# Ports for derived instances live above the default so they don't collide with
# a plain (no-instance) server on 4300. Range chosen to stay well clear of it.
_PORT_BASE = 4310
_PORT_SPAN = 600  # 4310–4909, hashed per instance id


class DesktopProfile(TypedDict):
    profile_id: str
    edition: str
    instance_id: str | None
    home_name: str
    tmux_socket: str
    port: int
    bundle_id: str
    app_name: str


def _port_for(iid: str | None) -> int:
    if not iid:
        return _DEFAULT_PORT
    digest = hashlib.sha256(iid.encode()).digest()
    return _PORT_BASE + int.from_bytes(digest[:4], "big") % _PORT_SPAN


def desktop_profile(edition: str, *, test: bool = False) -> DesktopProfile:
    """Pure build/runtime contract. This file can run without importing duckterm.

    Edition controls features; a profile controls ownership. The unqualified
    legacy CLI remains independent of these fixed desktop profiles.
    """
    if edition not in {"full", "lite", "code"}:
        raise ValueError("Duckterm edition must be lite, code, or full")
    iid = (
        ("test" if edition == "full" else f"test-{edition}")
        if test
        else (None if edition == "full" else edition)
    )
    suffix = (".test" if test else "") + ("" if edition == "full" else f".{edition}")
    return {
        "profile_id": iid or "full",
        "edition": edition,
        "instance_id": iid,
        "home_name": f".duckterm-{iid}" if iid else ".duckterm",
        "tmux_socket": f"duckterm-{iid}" if iid else "duckterm",
        "port": _port_for(iid),
        "bundle_id": "com.rubberduckhq.rubberterm" + suffix,
        "app_name": "DuckTerm"
        + ("" if edition == "full" else f" {edition.title()}")
        + (" Test" if test else ""),
    }


def configured_profile() -> DesktopProfile | None:
    raw = os.environ.get("DUCKTERM_DESKTOP_PROFILE", "")
    if not raw:
        return None
    for test in (False, True):
        for edition in ("full", "lite", "code"):
            profile = desktop_profile(edition, test=test)
            if profile["profile_id"] == raw:
                return profile
    raise ValueError(f"Unknown Duckterm desktop profile: {raw!r}")


def validate_desktop_context(edition: str) -> None:
    """Reject partial/mixed native profiles before touching state or processes."""
    profile = configured_profile()
    if profile is None:
        return
    expected_home = Path.home() / profile["home_name"]
    if (
        edition != profile["edition"]
        or instance_id() != profile["instance_id"]
        or home().resolve() != expected_home.resolve()
        or tmux_socket() != profile["tmux_socket"]
        or port() != profile["port"]
        or server_url() != f"http://127.0.0.1:{profile['port']}"
    ):
        raise ValueError(f"Conflicting environment for Duckterm profile {profile['profile_id']}")


def launch_env() -> dict[str, str]:
    """Complete context, including empty values that clear stale tmux state."""
    profile = configured_profile()
    return {
        "DUCKTERM_DESKTOP_PROFILE": profile["profile_id"] if profile else "",
        "DUCKTERM_INSTANCE": instance_id() or "",
        "DUCKTERM_HOME": str(home().resolve()),
        "DUCKTERM_TMUX_SOCKET": tmux_socket(),
        "DUCKTERM_PORT": str(port()),
        "DUCKTERM_URL": server_url(),
    }


def description() -> dict[str, object]:
    profile = configured_profile()
    return {
        "identity_version": 1,
        "profile_id": profile["profile_id"] if profile else None,
        "instance_id": instance_id(),
        "home": str(home().resolve()),
        "tmux_socket": tmux_socket(),
        "port": port(),
        "url": server_url(),
    }


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
    return _port_for(instance_id())


def server_url() -> str:
    """The base URL agents' hooks call back on (heartbeats, event ingest,
    approvals). Explicit DUCKTERM_URL wins; else this instance's own host:port —
    so a dev-launched agent never reports into prod."""
    override = os.environ.get("DUCKTERM_URL")
    if override:
        return override.rstrip("/")
    return f"http://127.0.0.1:{port()}"


def heartbeat_url() -> str:
    """The callback URL an agent's hooks post liveness to — this instance's own
    server, so a dev-launched agent never heartbeats into prod."""
    return f"{server_url()}/heartbeat"


if __name__ == "__main__":
    import argparse
    import json

    parser = argparse.ArgumentParser(description="Generate desktop profile build metadata")
    parser.add_argument("--desktop-profile", choices=("full", "lite", "code"), required=True)
    parser.add_argument("--test", action="store_true")
    args = parser.parse_args()
    profiles = [desktop_profile(e, test=t) for t in (False, True) for e in ("full", "lite", "code")]
    if len({p["port"] for p in profiles}) != len(profiles):
        parser.error("Canonical desktop profile ports collide")
    print(json.dumps(desktop_profile(args.desktop_profile, test=args.test)))
