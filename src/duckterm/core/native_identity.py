"""Which DuckTerm session a daemon-hosted hook event belongs to.

Codex 0.159+ runs every session's hooks in one shared daemon whose
environment is that of whichever session started it, so the hook sends no
session key (hook_host "daemon") and only the agent's own session_id. This
resolves that native id to a session ("Design — Shared-daemon identity",
2026-10-01): never by default, and never from env-derived history.

A native id resolves through an id DuckTerm itself recorded on the session
(HistoryStore.recorded_native_id), unless two sessions recorded it. It binds
once per launch, when a UserPromptSubmit carries exactly one live Codex
session's launch prompt and that session has no bind since its last launch
(a server-published SessionStart). An id bound before that launch can't
re-bind. The path is correlation evidence, never authentication: it only
attributes events already accepted from a local hook (architect's ruling,
2026-10-04). Every launch prompt names the
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
        # Each session's launch boundary when its binding was cached; a newer
        # launch (published elsewhere) invalidates the cache.
        self.launched: dict[str, int] = {}
        self.parked: deque[dict[str, Any]] = deque()
        self.parked_total = 0
        self.conflicted: set[str] = set()
        self.contested = 0

    def _codex_sessions(self) -> list[str]:
        return [
            str(r["session_key"])
            for r in self.history.sessions()
            if r.get("runtime") == "codex" and r.get("state") not in _OVER
        ]

    def _refresh(self) -> None:
        # One indexed lookup per live Codex session, not a scan of every event.
        # Two sessions claiming the same id is ambiguous: neither gets it.
        owners: dict[str, list[str]] = {}
        for key in self._codex_sessions():
            native = self.history.recorded_native_id(key)
            if native:
                owners.setdefault(native, []).append(key)
        self.bound = {n: ks[0] for n, ks in owners.items() if len(ks) == 1}
        self.launched = {k: self.history.last_launch_ts(k) for k in self.bound.values()}
        self.conflicted = {n for n, ks in owners.items() if len(ks) > 1}

    def _claim(self, raw: dict[str, Any]) -> str | None:
        """The one live Codex session whose launch prompt this is, if it may
        take a new native id: unbound, or relaunched since its last bind."""
        if raw.get("event_type") != "UserPromptSubmit":
            return None
        named = set(_INSTRUCTIONS.findall(str(raw.get("prompt") or "")))
        keys = [
            k for k in self._codex_sessions() if hashlib.sha256(k.encode()).hexdigest() in named
        ]
        if len(keys) > 1:
            self.contested += 1  # names several live sessions: park, and flag it
        if len(keys) != 1:
            return None
        key = keys[0]
        if str(raw.get("session_id")) in self.history.retired_native_ids(key):
            return None  # a previous generation's thread can't re-bind
        if key in self.bound.values():
            bind = self.history.last_event(key, NATIVE_BOUND)
            if bind is None or self.history.last_launch_ts(key) <= bind[1]:
                return None  # already bound in this launch: bind once
        return key

    def resolve(self, raw: dict[str, Any]) -> list[dict[str, Any]]:
        """The events to publish for one daemon-hosted hook event: none if it
        was parked, else any bind and replayed events, then the event itself
        with its session_key set."""
        now = int(time.time() * 1000)
        while self.parked and now - int(self.parked[0]["_parked_at"]) > PARK_MS:
            self.parked.popleft()
        native = str(raw.get("session_id") or "")
        out: list[dict[str, Any]] = []
        cached = self.bound.get(native)
        if cached is not None and self.history.last_launch_ts(cached) != self.launched.get(cached):
            cached = None  # relaunched since: its old binding no longer counts
        if native and cached is None:
            self._refresh()  # a bind recorded since, or since a restart
        key = self.bound.get(native) if native and native not in self.conflicted else None
        if key is None and native and native not in self.conflicted:
            key = self._claim(raw)
            if key is not None:
                self.bound = {n: k for n, k in self.bound.items() if k != key}
                self.bound[native] = key
                self.launched[key] = self.history.last_launch_ts(key)
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
        return {
            "parked": len(self.parked),
            "parked_total": self.parked_total,
            "contested": self.contested,
            "conflicted_ids": len(self.conflicted),
        }
