"""Server-side share bookkeeping: mint a share, start its uplink to the relay,
list/revoke. One ShareManager per running server.

A share = one running session exposed to the relay under a random share id, with
a separate random capability token the viewer must present. The token is NOT the
share id and never appears in a log — it rides in the viewer link's fragment.
Read-only: the manager only ever starts an uplink (push), never anything that
lets the relay drive the session.
"""

import os
import secrets
import time
from dataclasses import dataclass, field

from duckterm.sharing.uplink import RelayTarget, ShareUplink


def _relay_base() -> str:
    """Where the laptop dials the relay. Defaults to a local relay for dev."""
    return os.environ.get("RUBBERTERM_RELAY_URL", "ws://127.0.0.1:4400").rstrip("/")


def _device_token() -> str:
    """The laptop's identity to the relay. Env for now; the keychain-backed
    `rubberterm login` token replaces this (device-auth work)."""
    return os.environ.get("RUBBERTERM_DEVICE_TOKEN", "dev-relay-token")


@dataclass
class Share:
    share_id: str
    session_key: str
    token: str = field(repr=False)  # capability the viewer presents; never logged
    created_at: float = 0.0
    expires_at: float = 0.0
    uplink: ShareUplink = field(repr=False, default=None)  # type: ignore[assignment]

    def public(self, relay_web_base: str) -> dict[str, object]:
        """What the dashboard shows the owner. The link carries the token in the
        fragment so it stays out of request lines/logs."""
        return {
            "share_id": self.share_id,
            "session_key": self.session_key,
            "url": f"{relay_web_base}/s/{self.share_id}#{self.token}",
            "created_at": int(self.created_at),
            "expires_at": int(self.expires_at),
        }


# Default share lifetime: short, because the link is a bearer credential.
_DEFAULT_TTL = 24 * 3600


class ShareManager:
    def __init__(self, orchestrator: object, *, now: "callable | None" = None):  # type: ignore[valid-type]
        self._orch = orchestrator
        self._shares: dict[str, Share] = {}  # by share_id
        self._now = now or time.time

    def create(self, session_key: str, *, ttl: int = _DEFAULT_TTL) -> Share:
        """Expose a running session. Raises ValueError if it isn't live."""
        supervisor = self._orch.get(session_key)  # type: ignore[attr-defined]
        if supervisor is None or not getattr(supervisor, "running", False):
            raise ValueError("no live session to share")
        share_id = secrets.token_urlsafe(16)  # ≥128-bit, unguessable
        token = secrets.token_urlsafe(16)
        now = self._now()
        share = Share(
            share_id=share_id,
            session_key=session_key,
            token=token,
            created_at=now,
            expires_at=now + ttl,
        )
        target = RelayTarget(url=_relay_base(), share_id=share_id, device_token=_device_token())
        meta: dict[str, object] = {"name": session_key}
        share.uplink = ShareUplink(target, supervisor, meta)
        share.uplink.start()
        self._shares[share_id] = share
        return share

    def all_shares(self) -> list[Share]:
        self._sweep_expired()
        return list(self._shares.values())

    def for_session(self, session_key: str) -> list[Share]:
        return [s for s in self.all_shares() if s.session_key == session_key]

    async def revoke(self, share_id: str) -> bool:
        share = self._shares.pop(share_id, None)
        if share is None:
            return False
        await share.uplink.stop()
        return True

    async def revoke_for_session(self, session_key: str) -> int:
        """Drop every share of a session — call when it ends/stops/deletes so a
        share can't dangle pointing at a dead session."""
        gone = [s for s in self._shares.values() if s.session_key == session_key]
        for s in gone:
            await self.revoke(s.share_id)
        return len(gone)

    async def stop_all(self) -> None:
        for share_id in list(self._shares):
            await self.revoke(share_id)

    def _sweep_expired(self) -> None:
        now = self._now()
        for share_id, share in list(self._shares.items()):
            if share.expires_at <= now:
                share.uplink.request_stop()  # sync signal; task cancels itself
                self._shares.pop(share_id, None)
