"""One continuation budget; refuse overflow without silently removing context."""

from __future__ import annotations

import json
from typing import Any

from duckterm import prompts
from duckterm.core.session_api import APIError

MAX_BRIEF_BYTES = 32000


def render(
    *,
    introduction: str,
    launch: dict[str, Any],
    required: str,
    protected: dict[str, Any],
    narratives: list[dict[str, Any]],
    metadata: dict[str, Any],
    source_map: list[dict[str, str]],
    tools: str,
) -> str:
    """Keep the complete selected brief and tools or leave the source agent intact."""
    heading = prompts.render(
        "handoff_continuation",
        introduction=introduction,
        launch=json.dumps(launch, ensure_ascii=False),
        required=required,
        summary=json.dumps(protected, ensure_ascii=False),
        tools=tools,
    )
    text = (
        heading
        + "\nRecent summary narratives (oldest first):\n"
        + json.dumps(narratives, ensure_ascii=False)
        + "\nSource references (memory sources lists the full catalog):\n"
        + json.dumps(source_map, ensure_ascii=False)
        + "\nCoverage:\n"
        + json.dumps(metadata, ensure_ascii=False)
    )
    if len(text.encode()) > MAX_BRIEF_BYTES:
        kinds = sorted(
            {
                str(c.get("kind", field))
                for field, entries in protected.items()
                if isinstance(entries, list)
                for c in entries
                if isinstance(c, dict)
            }
        )
        raise APIError(
            409,
            "Complete handoff exceeds the 32,000-byte budget"
            + ("; protected kinds: " + ", ".join(kinds) if kinds else "")
            + ". No context was removed; source agent kept",
        )
    return text
