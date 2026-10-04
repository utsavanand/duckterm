"""Which DuckTerm session a daemon-hosted hook event belongs to.

Codex 0.159+ runs every session's hooks in one shared daemon whose
environment is that of whichever session started it, so the hook sends no
session key (hook_host "daemon") and only the agent's own session_id. This
resolves that native id to a session ("Design — Shared-daemon identity",
2026-10-01): never by default, and never from env-derived history.

A native id resolves through an id DuckTerm itself recorded on the session
(HistoryStore.recorded_native_id), or binds once, when a UserPromptSubmit
carries a live Codex session's launch prompt. Every launch prompt names the
session's instruction file, which lives under sha256(session key)
(session_instructions). The bind is recorded as a NativeBound event on that
session, so it survives restarts without a schema change. Anything else is
parked, with a count, and replayed if a bind arrives within PARK_MS.
"""

import hashlib
import re
import time
from collections import deque
from typing import TYPE_CHECKING, Any

from duckterm.core.events import NATIVE_BOUND

if TYPE_CHECKING:
    from duckterm.persistence.history import HistoryStore

PARK_MS = 10 * 60_000
PARK_LIMIT = 500
_INSTRUCTIONS = re.compile(r"session-instructions/([0-9a-f]{64})/collaboration\.md")
_OVER = ("archived", "terminated")


class NativeIdentity:
    def __init__(self, history: "HistoryStore") -> None:
        self.history = history
        self.bound: dict[str, str] = {}
        self.parked: deque[dict[str, Any]] = deque()
        self.parked_total = 0

    def _codex_sessions(self) -> list[str]:
        return [
            str(r["session_key"])
            for r in self.history.sessions()
            if r.get("runtime") == "codex" and r.get("state") not in _OVER
        ]

    def _refresh(self) -> None:
        # One indexed lookup per live Codex session, not a scan of every event.
        for key in self._codex_sessions():
            native = self.history.recorded_native_id(key)
            if native:
                self.bound[native] = key

    def _claim(self, raw: dict[str, Any]) -> str | None:
        if raw.get("event_type") != "UserPromptSubmit":
            return None
        match = _INSTRUCTIONS.search(str(raw.get("prompt") or ""))
        if not match:
            return None
        return next(
            (
                key
                for key in self._codex_sessions()
                if hashlib.sha256(key.encode()).hexdigest() == match[1]
            ),
            None,
        )

    def resolve(self, raw: dict[str, Any]) -> list[dict[str, Any]]:
        """The events to publish for one daemon-hosted hook event: none if it
        was parked, else any bind and replayed events, then the event itself
        with its session_key set."""
        now = int(time.time() * 1000)
        while self.parked and now - int(self.parked[0]["_parked_at"]) > PARK_MS:
            self.parked.popleft()
        native = str(raw.get("session_id") or "")
        out: list[dict[str, Any]] = []
        if native and native not in self.bound:
            self._refresh()  # a bind recorded since, or since a restart
        key = self.bound.get(native) if native else None
        if key is None and native:
            key = self._claim(raw)
            if key is not None:
                self.bound = {n: k for n, k in self.bound.items() if k != key}
                self.bound[native] = key
                out.append(
                    {"event_type": NATIVE_BOUND, "session_key": key, "native_session_id": native}
                )
                waiting = [p for p in self.parked if p.get("session_id") == native]
                self.parked = deque(p for p in self.parked if p.get("session_id") != native)
                out += [
                    {**{k: v for k, v in p.items() if k != "_parked_at"}, "session_key": key}
                    for p in waiting
                ]
        if key is None:
            self.parked.append({**raw, "_parked_at": now})
            self.parked_total += 1
            if len(self.parked) > PARK_LIMIT:
                self.parked.popleft()
            return []
        return [*out, {**raw, "session_key": key}]

    def status(self) -> dict[str, int]:
        return {"parked": len(self.parked), "parked_total": self.parked_total}
