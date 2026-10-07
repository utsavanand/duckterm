"""Observation exclusions must not hide meaningful source changes."""

import asyncio
import json

import pytest
from tests.runtime.test_memory import memory_rig
from tests.runtime.test_memory_preparation import preparation, prepare

__all__ = ["memory_rig", "preparation"]


def observe(server, **extra):
    server.history.record(
        {
            "_id": "qa-observation",
            "_ts": 500,
            "session_key": "agent",
            "test": True,
            "event_type": "Notification",
            "reconciled": True,
            "notification_type": "idle_prompt",
            **extra,
        }
    )


def test_metadata_only_idle_observation_preserves_ready_handoff(preparation):
    server, _, _ = preparation
    view = asyncio.run(prepare(server))
    assert view["state"] == "ready"
    observe(server)
    assert server.memory_preparation.status("agent", view["preparation_id"])["state"] == "ready"
    assert (
        server.history._conn.execute(
            "SELECT count(*) FROM events WHERE id='qa-observation'"
        ).fetchone()[0]
        == 1
    )


@pytest.mark.parametrize(
    "extra",
    [
        {"message": "Owner must review before publishing"},
        {"notification_type": "permission_prompt"},
        {"future_payload": {"constraint": "Do not publish"}},
    ],
)
def test_real_or_unknown_notification_payload_invalidates_readiness(preparation, extra):
    server, _, _ = preparation
    view = asyncio.run(prepare(server))
    assert view["state"] == "ready"
    observe(server, **extra)
    assert (
        server.memory_preparation.status("agent", view["preparation_id"])["state"] == "stale_source"
    )


@pytest.mark.parametrize("change", ["edit", "delete"])
def test_material_event_edit_or_deletion_cannot_be_hidden_by_new_observation(preparation, change):
    server, _, _ = preparation
    observe(server, message="Original owner constraint")
    view = asyncio.run(prepare(server))
    assert view["state"] == "ready"
    conn = server.history._conn
    with conn:
        if change == "delete":
            conn.execute("DELETE FROM events WHERE id='qa-observation'")
        else:
            payload = json.loads(
                conn.execute(
                    "SELECT payload_json FROM events WHERE id='qa-observation'"
                ).fetchone()[0]
            )
            payload["message"] = "Changed owner constraint"
            conn.execute(
                "UPDATE events SET payload_json=? WHERE id='qa-observation'", (json.dumps(payload),)
            )
    assert (
        server.memory_preparation.status("agent", view["preparation_id"])["state"] == "stale_source"
    )


@pytest.mark.parametrize("change", ["edit", "delete"])
def test_legacy_all_event_marker_still_detects_observation_source_loss(preparation, change):
    from duckterm.persistence.saved_state import event_source, fingerprint, resolve_checkpoint

    server, _, _ = preparation
    observe(server)
    view = asyncio.run(prepare(server))
    assert view["state"] == "ready"
    checkpoint = server.history.checkpoints("agent")[0]
    conn = server.history._conn
    checkpoint["record"] = json.loads(
        conn.execute(
            "SELECT record_json FROM checkpoints WHERE id=?", (checkpoint["id"],)
        ).fetchone()[0]
    )
    # Reconstruct the pre-filter wire boundary directly from its three columns,
    # independently of the new event_source helper's default/optional filter.
    rows = conn.execute(
        "SELECT rowid,id,payload_json FROM events WHERE session_key='agent' ORDER BY rowid"
    ).fetchall()
    old = {
        "first": rows[0][0],
        "last": rows[-1][0],
        "first_id": rows[0][1],
        "last_id": rows[-1][1],
        "count": len(rows),
        "fingerprint": fingerprint([list(row) for row in rows]),
    }
    assert event_source(conn, "agent", old["last"]) == old
    checkpoint["record"]["events"] = old
    initial = resolve_checkpoint(conn, "agent", checkpoint)
    assert initial["coverage"]["state"] == "retained", (
        old,
        initial["reason_codes"],
        initial["record"].keys(),
    )
    with conn:
        if change == "delete":
            conn.execute("DELETE FROM events WHERE id='qa-observation'")
        else:
            payload = json.loads(
                conn.execute(
                    "SELECT payload_json FROM events WHERE id='qa-observation'"
                ).fetchone()[0]
            )
            payload["_ts"] += 1
            conn.execute(
                "UPDATE events SET payload_json=? WHERE id='qa-observation'", (json.dumps(payload),)
            )
    lost = resolve_checkpoint(conn, "agent", checkpoint)
    assert lost["coverage"]["state"] == "missing"
    assert "source_missing" in lost["reason_codes"]
    assert not lost["handoff_eligible"]
