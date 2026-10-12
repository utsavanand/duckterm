"""Validate native TUI observations without guessing or rebinding a live card."""

from __future__ import annotations

import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from duckterm.runtimes.opencode_protocol import VERSION, OpenCodeError, native_id


@dataclass(frozen=True)
class Selection:
    """Stored launch identity. Transport authentication is checked before observe.

    The caller persists each returned state before submitting any native write.
    Once contested, an explicit owner adoption/new generation is required.
    """

    generation: str
    directory: str
    pid: int
    session_id: str
    sequence: int = 0
    selected: bool = False
    contested: bool = False
    observed_at: float = 0.0

    def __post_init__(self) -> None:
        native_id(self.session_id)
        if not self.generation or type(self.pid) is not int or self.pid <= 0:
            raise OpenCodeError("Missing native launch identity")
        if not Path(self.directory).is_absolute():
            raise OpenCodeError("Native launch needs a canonical project directory")
        if str(Path(self.directory).resolve()) != self.directory:
            raise OpenCodeError("Native launch directory must be canonical")

    def observe(self, event: dict[str, Any], *, now: float | None = None) -> Selection:
        """Stale/foreign events never grant readiness or change the mapping."""
        if (
            event.get("version") != VERSION
            or event.get("generation") != self.generation
            or type(event.get("pid")) is not int
            or event["pid"] != self.pid
            or event.get("directory") != self.directory
        ):
            raise OpenCodeError("Native observation does not match this launch")
        sequence = event.get("sequence")
        if type(sequence) is not int or sequence <= self.sequence:
            raise OpenCodeError("Native observation is stale or unordered")
        matches = (
            event.get("ready") is True
            and event.get("route") == "session"
            and event.get("sessionID") == self.session_id
        )
        contested = self.contested or (self.selected and not matches)
        return replace(
            self,
            sequence=sequence,
            selected=matches and not contested,
            contested=contested,
            observed_at=time.monotonic() if now is None else now,
        )

    def require_selected(self, *, now: float | None = None) -> None:
        age = (time.monotonic() if now is None else now) - self.observed_at
        if not self.selected or self.contested or not 0 <= age <= 5:
            raise OpenCodeError("The native TUI has not confirmed this conversation")
