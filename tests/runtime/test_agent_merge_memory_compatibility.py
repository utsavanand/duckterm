"""Isolated integration checks for memory 41f4a7f plus shipped agent merge."""

import json

from tests.runtime.test_agent_merges import request, rig, send
from tests.runtime.test_session_api import dispatch

__all__ = ["rig"]


def test_handoff_receipt_is_not_progress_or_graph_and_notes_are_current_memory(rig):
    history, server, _ = rig
    code, receipt = send(rig, request(rig))
    assert code == 200
    catalog = server.memory.catalog("source", retrieval=True)
    assert receipt["id"] not in {i["id"] for i in server.digests.items("source")}
    assert catalog["canonical"]["links"] == []
    assert catalog["canonical"]["revisions"] == []
    assert not any("agent_merge_v1" in json.dumps(s) for s in catalog["sources"])
    destination = server.memory.catalog("target", retrieval=True)
    facts = next(s for s in destination["sources"] if s["kind"] == "session")
    assert json.loads(facts["records"][0]["text"])["notes"] == history.session("target")["notes"]
    inbox = [s for s in destination["sources"] if s["kind"] == "inbox"]
    assert len(inbox) == 1
    assert receipt["packet"] == json.loads(inbox[0]["records"][0]["text"])["question"]
    assert inbox[0]["records"][0]["role"] == "owner"
    assert history._conn.execute(
        "SELECT text FROM digest_items WHERE id=?", (receipt["id"],)
    ).fetchone()


def test_merge_preview_uses_canonical_maintained_summary_over_compatibility_text(rig):
    history, server, auth = rig
    history._conn.execute("BEGIN")
    identity = server.digests.add_revision(
        "source",
        {
            "summary": "Current maintained context",
            "source": {},
            "conversation": {},
            "summary_validation": {},
            "continuity": {"verified": True, "context": {}, "processed": {}},
        },
        12,
    )
    history._conn.execute(
        "UPDATE sessions SET progress=? WHERE session_key=?",
        (json.dumps({"revision_id": identity, "summary": "Old compatibility text"}), "source"),
    )
    history._conn.commit()
    status, preview = dispatch(
        server, "POST", "/sessions/source/agent-merge/preview", auth, b'{"target":"target"}'
    )
    assert status == 200
    assert preview["context"] == "Current maintained context"
