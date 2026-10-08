"""Checkpoint persistence and switch safety share the same real summary pipeline."""

import asyncio
import json

import pytest

from duckterm import harness_switch
from duckterm.core.session_api import APIError
from duckterm.llm.summarizer import Summary
from duckterm.persistence.checkpoints import load_record
from duckterm.persistence.history import HistoryStore
from duckterm.server import Server


@pytest.fixture
def rig(tmp_path, monkeypatch):
    monkeypatch.setenv("DUCKTERM_HOME", str(tmp_path / "home"))
    history = HistoryStore(tmp_path / "db.sqlite")
    server = Server(history=history)
    server.bus.publish(
        {
            "event_type": "SessionStart",
            "session_key": "test-save",
            "runtime": "codex",
            "session_id": "test-conversation",
            "test": True,
            "cwd": str(tmp_path),
        }
    )
    server.history.set_meta("test-save", group="Tests", notes="Do not publish without review")
    text = [
        {"role": "user", "text": "Fix the bug; keep publishing behind review."},
        {"role": "assistant", "text": "The fix is tested. Review before publishing."},
    ]
    monkeypatch.setattr(server, "_progress_transcript", lambda *args: list(text))
    calls = []

    def provider(prompt):
        calls.append(prompt)
        if "validating a candidate" in prompt:
            return Summary(
                json.dumps(
                    {
                        "accept": [],
                        "done_next_action_ids": [],
                        "summary_validation": {"ready": True, "reason_codes": []},
                    }
                ),
                "stub",
            )
        return Summary(
            json.dumps(
                {
                    "summary": "Fix tested; review before publishing.",
                    "deliverables": [],
                    "next_actions": ["Review the fix"],
                }
            ),
            "stub",
        )

    monkeypatch.setattr("duckterm.server.summarize", provider)
    try:
        yield server, text, calls
    finally:
        history.delete_session("test-save")
        history.close()


def checkpoint(server):
    return server._create_checkpoint("test-save", server.history.session("test-save"), "manual")


def test_fresh_marker_reuses_revision_and_projects_original_events(rig):
    server, _, calls = rig

    async def run():
        first = await checkpoint(server)
        second = await checkpoint(server)
        assert first["saved"] and first["handoff_eligible"]
        assert second["summary_state"] == "ready"
        assert first["record"]["summary_ref"] == second["record"]["summary_ref"]
        assert len(calls) == 2  # generation+validation; zero extra for checkpoint two
        raw = server.history._conn.execute("SELECT summary,record_json FROM checkpoints").fetchone()
        assert raw["summary"] == ""
        stored = json.loads(raw["record_json"])
        assert not {"prompts", "commands", "tools", "files"} & stored.keys()
        assert first["record"]["event_count"] == 1
        assert first["summary"] in load_record(first["markdown_path"])
        assert server.digests.items("test-save") == []

    asyncio.run(run())


def test_unavailable_provider_saves_facts_but_cannot_prepare_switch(rig, monkeypatch):
    server, _, _ = rig
    monkeypatch.setattr("duckterm.server.summarize", lambda _: Summary("", "none"))

    async def run():
        cp = await checkpoint(server)
        assert cp["saved"] and cp["summary_state"] == "unavailable"
        assert not cp["handoff_eligible"]
        with pytest.raises(APIError, match="not ready"):
            await harness_switch.prepare(server, "test-save", server.history.session("test-save"))
        assert len(server.history.checkpoints("test-save")) == 2

    asyncio.run(run())


def test_unvalidated_paragraph_never_becomes_ready(rig, monkeypatch):
    server, _, _ = rig
    monkeypatch.setattr(
        "duckterm.server.summarize",
        lambda _: Summary(
            json.dumps(
                {"summary": "Generated but not verified", "accept": [], "done_next_action_ids": []}
            ),
            "stub",
        ),
    )
    cp = asyncio.run(checkpoint(server))
    assert cp["saved"] and cp["summary_state"] == "unverified"
    assert not cp["handoff_eligible"]


def test_changed_transcript_and_notes_invalidate_same_event_boundary(rig):
    server, text, _ = rig

    async def run():
        await checkpoint(server)
        prepared = await harness_switch.prepare(
            server, "test-save", server.history.session("test-save")
        )
        assert "Do not publish" in prepared["seed"]
        text[-1] = {"role": "assistant", "text": "Different text of about the same length."}
        with pytest.raises(APIError, match="sources changed"):
            await harness_switch.validate(server, "test-save", prepared)
        server.history.set_meta("test-save", notes="The owner added a new constraint")
        with pytest.raises(APIError, match="context changed"):
            harness_switch.validate_facts(server, "test-save", prepared)

    asyncio.run(run())


def test_provider_failure_keeps_last_good_summary_with_its_age(rig, monkeypatch):
    server, text, _ = rig

    async def run():
        first = await checkpoint(server)
        old = server.history.session("test-save")["progress"]
        text.append({"role": "user", "text": "An additional requirement"})
        monkeypatch.setattr("duckterm.server.summarize", lambda _: Summary("", "none"))
        second = await checkpoint(server)
        assert second["summary"] == first["summary"]
        assert second["summary_source_at"] == first["summary_source_at"]
        assert second["summary_state"] == "stale" and not second["handoff_eligible"]
        assert server.history.session("test-save")["progress"] == old

    asyncio.run(run())


def test_long_legacy_tail_and_required_context_overflow_block_handoff(rig):
    server, text, _ = rig
    text[0]["text"] = "X" * 9000
    cp = asyncio.run(checkpoint(server))
    assert "legacy_baseline_unreviewed" in cp["reason_codes"]
    assert not cp["handoff_eligible"]
    server.history.set_meta("test-save", notes="N" * 14000)
    cp = asyncio.run(checkpoint(server))
    assert "required_context_too_large" in cp["reason_codes"]
    assert not cp["handoff_eligible"]


def test_multibyte_notes_cannot_claim_ready_beyond_seed_byte_limit(rig):
    server, _, _ = rig
    # Fits the character bound, but cannot fit unabridged in the launch seed.
    server.history.set_meta("test-save", notes="🔒" * 6500)
    cp = asyncio.run(checkpoint(server))
    assert cp["saved"]
    assert "required_context_too_large" in cp["reason_codes"]
    assert not cp["handoff_eligible"]
    assert server.history.session("test-save")["notes"] == "🔒" * 6500


def test_handoff_mail_obeys_original_root_as_well_as_current_peer_grant(rig):
    from duckterm.core.saved_progress import required_context

    server, _, _ = rig
    conn = server.history._conn
    # A peer and owner sent these in a previous root; moving both sessions into
    # the current root must not re-grant their old mail to a new conversation.
    server.bus.publish({"event_type": "SessionStart", "session_key": "test-peer", "test": True})
    try:
        server.history.set_meta("test-peer", group="Tests")
        server.history.session_api.enroll("test-save", {"root": "Tests"})
        server.history.session_api.enroll("test-peer", {"root": "Tests"})
        with conn:
            for identity, sender, root in (
                ("old-owner", "owner", "OldTeam"),
                ("old-peer", "test-peer", "OldTeam"),
                ("new-owner", "owner", "Tests"),
                ("new-peer", "test-peer", "Tests"),
            ):
                conn.execute(
                    "INSERT INTO session_questions "
                    "(id,sender,recipient,sender_name,root,question,created_at,expires_at,"
                    "idempotency_key,content_hash) VALUES (?,?,?,?,?,?,1,0,?,?)",
                    (identity, sender, "test-save", sender, root, identity, identity, identity),
                )
        context = required_context(server, "test-save", server.history.session("test-save"))
        assert {m["id"] for m in context["mail"]} == {"new-owner", "new-peer"}
        server.history.set_meta("test-peer", group="OtherTeam")
        context = required_context(server, "test-save", server.history.session("test-save"))
        assert {m["id"] for m in context["mail"]} == {"new-owner"}
    finally:
        server.history.delete_session("test-peer")


def test_marker_detects_interior_source_loss_and_legacy_remains_readable(rig):
    server, _, _ = rig
    for identity in ("middle", "last"):
        server.bus.publish(
            {
                "_id": identity,
                "event_type": "UserPromptSubmit",
                "session_key": "test-save",
                "prompt": identity,
                "test": True,
            }
        )
    cp = asyncio.run(checkpoint(server))
    assert cp["handoff_eligible"]
    with server.history._conn:
        removed = server.history._conn.execute(
            "DELETE FROM events WHERE json_extract(payload_json,'$.prompt')='middle'"
        )
        assert removed.rowcount == 1
    resolved = server.history.checkpoints("test-save")[0]
    assert not resolved["handoff_eligible"]
    assert resolved["coverage"]["state"] == "missing"
    server.history.add_checkpoint(
        checkpoint_id="old",
        session_key="test-save",
        label="legacy",
        summary="Original legacy text",
        record={"prompts": ["Original prompt"], "files": [], "tools": [], "event_count": 1},
        markdown_path=None,
        created_at=1,
    )
    old = next(c for c in server.history.checkpoints("test-save") if c["id"] == "old")
    assert old["summary"] == "Original legacy text"
    assert old["record"]["prompts"] == ["Original prompt"]
    assert old["summary_state"] == "legacy-unverified" and not old["handoff_eligible"]


def test_explicit_session_deletion_removes_revision_rows(rig):
    server, _, _ = rig
    asyncio.run(checkpoint(server))
    assert server.history._conn.execute("SELECT count(*) FROM digest_items").fetchone()[0] == 1
    server.history.delete_session("test-save")
    assert server.history._conn.execute("SELECT count(*) FROM digest_items").fetchone()[0] == 0
    assert not server.history.checkpoints("test-save")


@pytest.mark.parametrize("work_only", [False, True])
def test_event_boundary_version_preserves_old_markers_and_detects_real_edits(rig, work_only):
    from duckterm.persistence.saved_state import event_source, resolve_checkpoint

    server, _, _ = rig
    for identity, event_type in [("observed", "Attended"), ("real", "UserPromptSubmit")]:
        payload = {
            "_id": identity,
            "_ts": 100,
            "event_type": event_type,
            "session_key": "test-save",
            "test": True,
        }
        if identity == "real":
            payload["prompt"] = "Retain this constraint"
        server.history.record(payload)
    cp = asyncio.run(checkpoint(server))
    raw = dict(cp)
    raw["record"] = json.loads(
        server.history._conn.execute(
            "SELECT record_json FROM checkpoints WHERE id=?", (cp["id"],)
        ).fetchone()[0]
    )
    raw["record"]["events"] = event_source(server.history._conn, "test-save", work_only=work_only)
    # Exercise the original unfiltered interpretation as well as new work-only markers.
    raw["record"]["summary_ref"] = None
    resolved = resolve_checkpoint(server.history._conn, "test-save", raw)
    assert resolved["coverage"]["state"] == "retained"
    assert resolved["record"]["event_count"] == (2 if work_only else 3)
    with server.history._conn:
        server.history._conn.execute("DELETE FROM events WHERE id='observed'")
    resolved = resolve_checkpoint(server.history._conn, "test-save", raw)
    assert resolved["coverage"]["state"] == ("retained" if work_only else "missing")
    with server.history._conn:
        server.history._conn.execute(
            "UPDATE events SET payload_json=json_set(payload_json,'$.prompt',"
            "'Changed owner constraint') WHERE id='real'"
        )
    assert (
        resolve_checkpoint(server.history._conn, "test-save", raw)["coverage"]["state"] == "missing"
    )


def test_unknown_event_filter_cannot_claim_retained_coverage(rig):
    from duckterm.persistence.saved_state import resolve_checkpoint

    server, _, _ = rig
    cp = asyncio.run(checkpoint(server))
    cp["record"]["events"]["filter"] = "unrecognized-future-version"
    resolved = resolve_checkpoint(server.history._conn, "test-save", cp)
    assert resolved["coverage"]["state"] == "missing"
    assert not resolved["handoff_eligible"]


@pytest.mark.parametrize(
    "reason", ["provider_timeout", "provider_failed", "disabled", "no_provider"]
)
def test_manual_checkpoint_records_failed_summary_attempt_without_inventing_success(
    rig, monkeypatch, reason
):
    server, _, _ = rig
    monkeypatch.setattr(
        "duckterm.server.summarize", lambda _: Summary("", "none", failure_reason=reason)
    )
    cp = asyncio.run(checkpoint(server))
    assert cp["saved"]  # the marker/history boundary survived, not the summary update
    assert cp["summary_update"]["state"] == "failed"
    assert cp["summary_update"]["reason"] == reason
    assert cp["summary_update"]["revision_id"] is None
    assert cp["summary_update"]["attempted_at"] == cp["created_at"]
    assert server.history.checkpoints("test-save")[0]["summary_update"] == cp["summary_update"]
    assert cp["record"]["event_count"] == 1
    assert cp["record"]["summary_ref"] is None


def test_manual_checkpoint_distinguishes_updated_reused_and_failed_with_old_summary(
    rig, monkeypatch
):
    server, text, calls = rig

    async def run():
        first = await checkpoint(server)
        assert first["summary_update"]["state"] == "updated"
        second = await checkpoint(server)
        assert second["summary_update"]["state"] == "reused"
        assert second["summary_update"]["revision_id"] == first["record"]["summary_ref"]
        assert len(calls) == 2
        before = server.history.session("test-save")["progress"]
        text.append({"role": "user", "text": "A later constraint"})
        monkeypatch.setattr(
            "duckterm.server.summarize",
            lambda _: Summary("", "none", failure_reason="provider_timeout"),
        )
        failed = await checkpoint(server)
        assert failed["summary_update"]["state"] == "failed"
        assert failed["summary_update"]["revision_id"] is None
        assert failed["summary"] == first["summary"]
        assert failed["summary_source_at"] == first["summary_source_at"]
        assert server.history.session("test-save")["progress"] == before

    asyncio.run(run())


def test_manual_checkpoint_does_not_store_raw_provider_output_as_a_failure_reason(rig, monkeypatch):
    server, _, _ = rig
    monkeypatch.setattr(
        "duckterm.server.summarize", lambda _: Summary("private invalid response", "cli")
    )
    cp = asyncio.run(checkpoint(server))
    assert cp["summary_update"]["state"] == "failed"
    assert cp["summary_update"]["reason"] == "invalid_summary"
    raw = server.history._conn.execute(
        "SELECT record_json FROM checkpoints WHERE id=?", (cp["id"],)
    ).fetchone()[0]
    assert "private invalid response" not in raw


def test_rejected_candidate_keeps_last_good_checkpoint_summary(rig, monkeypatch):
    server, text, _ = rig

    async def run():
        first = await checkpoint(server)
        text.append({"role": "user", "text": "An additional constraint"})
        monkeypatch.setattr(
            "duckterm.server.summarize",
            lambda _: Summary(json.dumps({"summary": "Unverified replacement"}), "stub"),
        )
        failed = await checkpoint(server)
        assert failed["summary_update"]["state"] == "failed"
        assert failed["summary_update"]["reason"] == "summary_unverified"
        assert failed["record"]["summary_ref"] == first["record"]["summary_ref"]
        assert failed["summary"] == first["summary"]
        assert failed["summary_source_at"] == first["summary_source_at"]
        assert failed["summary_state"] == "stale"
        assert not failed["handoff_eligible"]

    asyncio.run(run())
