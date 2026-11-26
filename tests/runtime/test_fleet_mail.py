"""The Fleet monitor's message arrows: recent agent-to-agent mail, who wrote to
whom and when, never the text, with owner sends and test sessions left out."""

import time

from tests.runtime.test_relay import world  # noqa: F401  (fixture)
from tests.runtime.test_session_api import dispatch

from duckterm.editions import EditionConfig


def mail(history, qid, sender, recipient, age_ms):
    history.session_api.conn.execute(
        "INSERT INTO session_questions (id, sender, recipient, sender_name, root, question, "
        "created_at, expires_at, idempotency_key, content_hash) "
        "VALUES (?, ?, ?, ?, 'Duckterm', 'private text', ?, 0, ?, 'h')",
        (qid, sender, recipient, sender, int(time.time() * 1000) - age_ms, qid),
    )


def test_route_needs_the_flag_and_returns_only_recent_agent_mail(world) -> None:  # noqa: F811
    server, owner, _, history = world
    for i, key in enumerate(("dev", "qa")):
        history.record({"_id": f"m{i}", "_ts": 5, "event_type": "SessionStart", "session_key": key})
    mail(history, "q-new", "dev", "qa", 5_000)
    mail(history, "q-older", "qa", "dev", 60_000)
    mail(history, "q-owner", "owner", "qa", 5_000)
    mail(history, "q-test", "pm", "qa", 5_000)  # pm is a test session
    mail(history, "q-old", "dev", "qa", 11 * 60_000)
    assert dispatch(server, "GET", "/fleet/mail", owner)[0] == 403
    server.edition = EditionConfig(server.edition.name, server.edition.features | {"fleet_monitor"})
    status, body = dispatch(server, "GET", "/fleet/mail", owner)
    assert status == 200
    assert [(m["id"], m["sender"], m["recipient"]) for m in body["messages"]] == [
        ("q-new", "dev", "qa"),
        ("q-older", "qa", "dev"),
    ]
    assert set(body["messages"][0]) == {"id", "sender", "recipient", "kind", "created_at"}
