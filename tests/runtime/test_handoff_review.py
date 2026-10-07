"""Owner-reviewed briefs cover one exact boundary, never unseen later work."""

import asyncio
import json

import pytest
from tests.runtime.test_saved_checkpoint import checkpoint, rig
from tests.runtime.test_session_api import Writer, dispatch

from duckterm import harness_switch
from duckterm.core.session_api import APIError
from duckterm.handoff_review import REVIEW_SECONDS

__all__ = ["rig"]


def approval(packet, **changes):
    return {
        "review_id": packet["review_id"],
        "approve": True,
        "packet_hash": packet["packet_hash"],
        "request_key": packet["review_id"],
        **changes,
    }


async def approved(server):
    packet = await server.handoff_review.prepare(
        "test-save",
        {
            "summary": "Review the verified candidate before publishing.",
            "gaps": ["Native history stays with the old harness."],
        },
    )
    result = await server.handoff_review.approve("test-save", approval(packet))
    return packet, result


def test_long_reviewed_brief_uses_one_revision_without_provider_and_can_prepare(rig, monkeypatch):
    server, transcript, _ = rig
    transcript[0]["text"] = "Long legacy conversation. " * 2000

    def no_provider(_):
        raise AssertionError("Explicit review must not require an available LLM")

    monkeypatch.setattr("duckterm.server.summarize", no_provider)

    async def run():
        packet, result = await approved(server)
        assert result["saved"] and packet["coverage"] == "owner-selected"
        assert "not the full conversation" in packet["warning"]
        cp = await checkpoint(server)
        assert cp["saved"] and cp["handoff_eligible"]
        assert cp["summary_origin"] == "owner-reviewed"
        assert cp["record"]["summary_ref"] == result["revision_id"]
        prepared = await harness_switch.prepare(
            server, "test-save", server.history.session("test-save")
        )
        assert packet["brief"] in prepared["seed"]
        assert "Known gaps" in prepared["seed"]
        assert server.history._conn.execute("SELECT count(*) FROM digest_items").fetchone()[0] == 1

    asyncio.run(run())


@pytest.mark.parametrize("change", ["transcript", "notes", "policy", "progress", "scope"])
def test_changes_after_display_block_review_without_writing(rig, monkeypatch, change):
    server, transcript, _ = rig

    async def run():
        packet = await server.handoff_review.prepare(
            "test-save", {"summary": "Ready for review", "gaps": []}
        )
        if change == "transcript":
            transcript.append({"role": "user", "text": "New instruction"})
        elif change == "notes":
            server.history.set_meta("test-save", notes="New requirement")
        elif change == "policy":
            monkeypatch.setenv("DUCKTERM_SUMMARIZER_CMD", "changed")
        elif change == "progress":
            await server.progress_coordinator.refresh("test-save")
        else:
            server.history.set_meta("test-save", group="Changed")
        before = server.history._conn.total_changes
        with pytest.raises(APIError, match="changed"):
            await server.handoff_review.approve("test-save", approval(packet))
        assert server.history._conn.total_changes == before

    asyncio.run(run())


def test_new_long_history_is_not_certified_by_earlier_owner_review(rig):
    server, transcript, _ = rig
    transcript[0]["text"] = "Old history " * 2000

    async def run():
        _, result = await approved(server)
        transcript.append({"role": "user", "text": "Additional work not covered by review"})
        cp = await checkpoint(server)
        assert not cp["handoff_eligible"]
        assert "legacy_baseline_unreviewed" in cp["reason_codes"]
        # An ordinary 400-character digest must not erase the reviewed baseline.
        cached = json.loads(server.history.session("test-save")["progress"])
        assert cached["revision_id"] == result["revision_id"]
        assert "Native history stays" in cached["summary"]

    asyncio.run(run())


def test_owner_review_expiry_wrong_session_and_nonapproval_are_rejected(rig, monkeypatch):
    server, _, _ = rig

    async def run():
        packet = await server.handoff_review.prepare(
            "test-save", {"summary": "Review first", "gaps": []}
        )
        body = approval(packet)
        with pytest.raises(APIError):
            await server.handoff_review.approve("other", body)
        with pytest.raises(APIError):
            await server.handoff_review.approve("test-save", {**body, "approve": False})
        expiry = server.handoff_review.drafts[packet["review_id"]]["expires"]
        monkeypatch.setattr(
            "duckterm.handoff_review.time.monotonic", lambda: expiry + REVIEW_SECONDS
        )
        with pytest.raises(APIError, match="expired"):
            await server.handoff_review.approve("test-save", body)
        assert server.history._conn.execute("SELECT count(*) FROM digest_items").fetchone()[0] == 0

    asyncio.run(run())


def test_review_cannot_silently_truncate_required_context(rig):
    server, _, _ = rig
    server.history.set_meta("test-save", notes="🔒" * 6500)
    with pytest.raises(APIError, match="too large"):
        asyncio.run(server.handoff_review.prepare("test-save", {"summary": "Review", "gaps": []}))
    assert server.handoff_review.drafts == {}


def test_review_routes_require_owner_and_explicit_approval(rig):
    server, _, _ = rig
    path = "/sessions/test-save/handoff-review"
    for method in ("GET", "POST"):
        assert dispatch(server, method, path, {})[0] == 401
        assert dispatch(server, method, path, {"authorization": "Bearer agent"})[0] == 403
    headers = {"x-duckterm-token": server.token}
    assert dispatch(server, "GET", path, headers)[0] == 405
    writer = Writer()

    async def cross_origin():
        await server._dispatch(
            "POST",
            path,
            asyncio.StreamReader(),
            writer,
            {**headers, "origin": "https://evil.invalid"},
            b"{}",
        )

    asyncio.run(cross_origin())
    assert writer.data.split()[1] == b"403"
    assert dispatch(server, "POST", path, headers, b'{"summary":"Ready"}')[0] == 400


def test_owner_http_approval_is_idempotent_and_persists_checkpoint(rig, tmp_path):
    from duckterm.handoff_review import HandoffReview
    from duckterm.persistence.history import HistoryStore

    server, _, _ = rig
    headers = {"x-duckterm-token": server.token}
    path = "/sessions/test-save/handoff-"
    status, packet = dispatch(
        server,
        "POST",
        path + "review",
        headers,
        json.dumps({"summary": "Reviewed work and constraints", "gaps": []}).encode(),
    )
    assert status == 200
    request = approval(packet)
    status, result = dispatch(
        server, "POST", path + "approve", headers, json.dumps(request).encode()
    )
    assert status == 200 and result["checkpoint"]["handoff_eligible"]
    assert result["checkpoint"]["markdown_path"]
    server.handoff_review = HandoffReview(server)  # draft cache was lost
    status, again = dispatch(
        server, "POST", path + "approve", headers, json.dumps(request).encode()
    )
    assert status == 200 and again == result
    status, _ = dispatch(
        server,
        "POST",
        path + "approve",
        headers,
        json.dumps({**request, "packet_hash": "other"}).encode(),
    )
    assert status == 409
    reopened = HistoryStore(tmp_path / "db.sqlite")
    try:
        checkpoints = reopened.checkpoints("test-save")
        assert len(checkpoints) == 1 and checkpoints[0]["id"] == result["checkpoint"]["id"]
        assert checkpoints[0]["summary_origin"] == "owner-reviewed"
    finally:
        reopened.close()


def test_checkpoint_failure_rolls_back_review_cache_and_revision(rig):
    import sqlite3

    server, _, _ = rig
    conn = server.history._conn
    conn.execute(
        "CREATE TRIGGER fail_marker BEFORE INSERT ON checkpoints "
        "BEGIN SELECT RAISE(ABORT,'marker failed'); END"
    )
    conn.commit()
    with pytest.raises(sqlite3.IntegrityError, match="marker failed"):
        asyncio.run(approved(server))
    assert conn.execute("SELECT count(*) FROM digest_items").fetchone()[0] == 0
    assert conn.execute("SELECT count(*) FROM checkpoints").fetchone()[0] == 0
    assert server.history.session("test-save")["progress"] is None


def test_new_short_history_also_requires_review_and_preserves_long_brief(rig):
    server, transcript, _ = rig

    async def run():
        _, result = await approved(server)
        transcript.append({"role": "user", "text": "New owner instruction"})
        cp = await checkpoint(server)
        assert not cp["handoff_eligible"]
        assert "legacy_baseline_unreviewed" in cp["reason_codes"]
        assert (
            json.loads(server.history.session("test-save")["progress"])["revision_id"]
            == result["revision_id"]
        )

    asyncio.run(run())


def test_concurrent_approval_retry_creates_one_revision_and_marker(rig):
    server, _, _ = rig

    async def run():
        packet = await server.handoff_review.prepare(
            "test-save", {"summary": "Review first", "gaps": []}
        )
        left, right = await asyncio.gather(
            server.handoff_review.approve("test-save", approval(packet)),
            server.handoff_review.approve("test-save", approval(packet)),
        )
        assert left["revision_id"] == right["revision_id"]
        assert left["checkpoint"]["id"] == right["checkpoint"]["id"]
        assert server.history._conn.execute("SELECT count(*) FROM digest_items").fetchone()[0] == 1
        assert server.history._conn.execute("SELECT count(*) FROM checkpoints").fetchone()[0] == 1

    asyncio.run(run())
