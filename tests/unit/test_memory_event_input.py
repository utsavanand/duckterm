"""A hook arriving is not itself narrative that must be summarized."""

from duckterm.memory_summary_input import event_text


def test_stop_envelope_without_agent_text_is_reference_only():
    for kind in ("Stop", "SubagentStop"):
        assert event_text({"event_type": kind, "session_id": "id", "agent_pid": 123}) == ""
        assert (
            event_text({"event_type": kind, "last_assistant_message": "Gate passed."})
            == "Gate passed."
        )


def test_user_and_failure_statements_survive_without_tool_success_bodies():
    assert (
        event_text({"event_type": "UserPromptSubmit", "prompt": "Keep this constraint."})
        == "Keep this constraint."
    )
    assert "disk full" in event_text({"event_type": "PostToolUseFailure", "error": "disk full"})
    assert event_text({"event_type": "PostToolUse", "tool_response": "Many lines."}) == ""


def test_permission_request_is_not_a_permission_decision():
    assert (
        event_text({"event_type": "PermissionRequest", "tool_input": {"command": "deploy"}}) == ""
    )
    assert '"decision": "deny"' in event_text(
        {"event_type": "PermissionRequest", "decision": "deny"}
    )
    assert (
        event_text({"event_type": "Notification", "sender_kind": "peer", "text": "Do this"}) == ""
    )
    assert (
        event_text({"event_type": "Notification", "sender_kind": "owner", "text": "Keep it"})
        == "Keep it"
    )
