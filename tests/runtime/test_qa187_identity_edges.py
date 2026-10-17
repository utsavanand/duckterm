# ruff: noqa: F401, F811, I001 -- pytest fixture import (main-qa acceptance file)
"""Independent fail-closed checks for daemon identity ambiguity."""

from tests.runtime.test_native_identity import (
    server,
    post,
    filed,
    launch_prompt,
    NATIVE_A,
    NATIVE_B,
)  # noqa: F401


def test_launch_marker_does_not_rebind_an_already_bound_card(server):
    post(server, "UserPromptSubmit", NATIVE_A, prompt=launch_prompt("cx-a"))
    before = filed(server, "cx-a")
    result = post(server, "UserPromptSubmit", NATIVE_B, prompt=launch_prompt("cx-a"))
    assert result == {"parked": "unattributed daemon event"}
    assert filed(server, "cx-a") == before


def test_two_launch_markers_are_ambiguous_not_first_match(server):
    result = post(
        server,
        "UserPromptSubmit",
        NATIVE_A,
        prompt=launch_prompt("cx-a") + "\n" + launch_prompt("cx-b"),
    )
    assert result == {"parked": "unattributed daemon event"}
    assert filed(server, "cx-a") == filed(server, "cx-b") == []


def test_duplicate_recorded_native_id_is_parked(server):
    for i, key in enumerate(("cx-a", "cx-b")):
        server.history.record(
            {
                "_id": f"dup-{i}",
                "_ts": 100 + i,
                "event_type": "SessionStart",
                "session_key": key,
                "session_id": NATIVE_A,
                "runtime": "codex",
                "test": True,
            }
        )
    result = post(server, "PreToolUse", NATIVE_A, tool_name="Bash")
    assert result == {"parked": "unattributed daemon event"}
