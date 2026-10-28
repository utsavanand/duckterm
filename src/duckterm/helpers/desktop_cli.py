"""Apply an app-local CLI default without stealing an enrolled session's target."""

import json
import os
from pathlib import Path
from urllib.parse import urlsplit

from duckterm.helpers import instance, session_credentials

_CONTEXT = (
    "DUCKTERM_DESKTOP_PROFILE",
    "DUCKTERM_INSTANCE",
    "DUCKTERM_HOME",
    "DUCKTERM_TMUX_SOCKET",
    "DUCKTERM_PORT",
    "DUCKTERM_URL",
)


def apply_app_profile() -> None:
    """The wrapper supplies only its compiled DUCKTERM_APP_PROFILE default.

    Credential validation is local and never reads the owner token or calls an
    unrelated server. Legacy global CLI behavior is unchanged without this key.
    """
    default = os.environ.get("DUCKTERM_APP_PROFILE")
    if not default:
        return
    if os.environ.get("DUCKTERM_SESSION_KEY"):
        credential = os.environ.get("DUCKTERM_SESSION_TOKEN_FILE")
        if (
            credential
            and not Path(credential).exists()
            and all(name in os.environ for name in _CONTEXT)
        ):
            from duckterm import editions
            from duckterm.helpers import profile_state

            # Lite intentionally has callback identity but no inbox enrollment.
            # Its complete, owned workspace context must still work in a pane.
            if not editions.configured().includes("inbox"):
                if instance.configured_profile() and profile_state.validate_home():
                    raise ValueError("Lite session workspace ownership is unavailable")
                return
        _retain_enrollment()
        return
    if any(name in os.environ for name in _CONTEXT):
        return  # Explicit custom CLI targeting remains available.
    for test in (False, True):
        for edition in ("full", "lite", "code"):
            profile = instance.desktop_profile(edition, test=test)
            if profile["profile_id"] != default:
                continue
            os.environ.update(
                DUCKTERM_DESKTOP_PROFILE=default,
                DUCKTERM_INSTANCE=profile["instance_id"] or "",
                DUCKTERM_HOME=str(Path.home() / profile["home_name"]),
                DUCKTERM_TMUX_SOCKET=profile["tmux_socket"],
                DUCKTERM_PORT=str(profile["port"]),
                DUCKTERM_URL=f"http://127.0.0.1:{profile['port']}",
            )
            # An explicit edition is validated against the fixed app profile.
            os.environ.setdefault("DUCKTERM_EDITION", edition)
            return
    raise ValueError(f"Unknown app profile: {default!r}")


def _retain_enrollment() -> None:
    key = os.environ["DUCKTERM_SESSION_KEY"]
    path = Path(
        os.environ.get("DUCKTERM_SESSION_TOKEN_FILE") or session_credentials.credential_path(key)
    )
    if (
        path.name != session_credentials.credential_path(key).name
        or path.parent.name != "session-credentials"
    ):
        raise ValueError("Cannot verify this session's workspace; leave its CLI target unchanged")
    url, _token = session_credentials.client_credentials()
    root = path.resolve().parent.parent
    connection = path.parent / "server.json"
    metadata = json.loads(connection.read_text()) if connection.exists() else {}
    identity = metadata.get("instance")
    endpoint = urlsplit(url)
    if not endpoint.hostname or endpoint.port is None:
        raise ValueError("Cannot verify enrolled session server URL")
    if identity is not None:
        if (
            not isinstance(identity, dict)
            or identity.get("identity_version") != 1
            or identity.get("home") != str(root)
        ):
            raise ValueError("Session workspace does not match its server identity")
        iid = identity.get("instance_id")
        profile = identity.get("profile_id")
        sock = identity.get("tmux_socket")
        if (
            not isinstance(sock, str)
            or not sock
            or (iid is not None and not isinstance(iid, str))
            or (profile is not None and not isinstance(profile, str))
        ):
            raise ValueError("Invalid enrolled session identity")
    else:
        # Legacy enrollment predates the identity document. Its private file
        # supplies the home and server URL; the old default remains unnamed.
        iid = root.name.removeprefix(".duckterm-") if root.name.startswith(".duckterm-") else None
        profile = None
        sock = f"duckterm-{iid}" if iid else "duckterm"
    os.environ.update(
        DUCKTERM_HOME=str(root),
        DUCKTERM_INSTANCE=iid or "",
        DUCKTERM_DESKTOP_PROFILE=profile or "",
        DUCKTERM_TMUX_SOCKET=sock,
        DUCKTERM_PORT=str(endpoint.port),
        DUCKTERM_URL=url,
    )
    # The session's owning workspace, not this executable's bundle, selects
    # features. Optional CLI handlers read its saved edition configuration.
    os.environ.pop("DUCKTERM_EDITION", None)
