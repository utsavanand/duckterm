"""Internal BYOK reaches real checkpoint publication without changing the agent."""

import asyncio
import io
import json
from pathlib import Path

import pytest
from test_maintained_progress import append
from test_memory import memory_rig, transcript

from duckterm import checkpoint_settings, connectors, memory_provider, memory_summary
from duckterm.core import memory_refresh
from duckterm.core.memory_refresh import reconcile_reply
from duckterm.editions import CODE, FULL, LITE
from duckterm.helpers.session_credentials import credential_path
from duckterm.llm import ai_settings, api, backend

__all__ = ["memory_rig"]


@pytest.fixture(params=[LITE, CODE, FULL], ids=lambda edition: edition.name, autouse=True)
def edition(request, monkeypatch, tmp_path):
    # Select before constructing the real server and its session authorization.
    monkeypatch.setenv("DUCKTERM_EDITION", request.param.name)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    return request.param


class Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


def test_internal_api_checkpoint_preserves_agent_and_rejects_unfounded_draft(
    memory_rig, monkeypatch, edition
):
    server, agent_headers, _, tmp = memory_rig
    assert server.edition is server.history.session_api.edition is edition
    for name in (
        "DUCKTERM_SUMMARIZER",
        "DUCKTERM_SUMMARIZER_CMD",
        "DUCKTERM_SUMMARIZER_URL",
        "ANTHROPIC_API_KEY",
        "OPENAI_API_KEY",
        "OPENROUTER_API_KEY",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("DUCKTERM_NO_KEYCHAIN", "1")
    secrets = {}
    monkeypatch.setattr(connectors, "load_secret", secrets.get)
    monkeypatch.setattr(connectors, "save_secret", secrets.__setitem__)
    monkeypatch.setattr(connectors, "delete_secret", lambda key: secrets.pop(key, None))
    # Undo only the global no-op reconcile fixture: this test follows the real
    # maintained-memory path all the way to the mocked provider's wire response.
    monkeypatch.setattr(memory_refresh, "reconcile_reply", reconcile_reply)
    monkeypatch.setattr(
        backend.shutil, "which", lambda name: "/fixture/claude" if name == "claude" else None
    )
    harness_calls = []

    async def unexpected_harness(*args, **kwargs):
        harness_calls.append((args, kwargs))
        raise AssertionError("Internal API refresh must not invoke an agent harness")

    monkeypatch.setattr(memory_provider, "generate", unexpected_harness)

    # All bytes below are synthetic. A separate internal AI key must not replace
    # either harness's login or rotate the active agent's scoped credential.
    credentials = {
        tmp / ".claude" / ".credentials.json": b'{"fixture":"claude-login"}',
        tmp / ".codex" / "auth.json": b'{"fixture":"codex-login"}',
    }
    for path, value in credentials.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(value)
    scoped = credential_path("agent", server.history.session_api.credential_dir)
    credentials[scoped] = scoped.read_bytes()
    original = server.history.session("agent")
    identity = {key: original[key] for key in ("runtime", "cwd", "grp", "started_at")}
    native_id = server.history.session_id_for("agent")
    native_binding = server.history.restart_control("agent").get("native_binding")
    assert identity["runtime"] == "claude-code"
    assert server.history.session_api.authenticate(agent_headers) == "agent"
    ai_settings.save({"backend": "api", "fast_model": "fixture-fast", "deep_model": "fixture-deep"})
    ai_settings.save_key("anthropic", "fixture-internal-key")
    checkpoint_settings.save({"writer_model": "opus"})
    path = transcript(tmp, "agent-claude", "Keep originals when preserving the current work.")
    wire = []
    reject_draft = False
    invalid_marker = "Unfounded replacement must never become maintained memory."

    def respond(request, timeout):
        body = json.loads(request.data)
        prompt = body["messages"][0]["content"]
        writer = "MEMORY UPDATE:\n" in prompt
        wire.append(
            {
                "url": request.full_url,
                "headers": {key.lower(): value for key, value in request.header_items()},
                "body": body,
                "writer": writer,
            }
        )
        if writer:
            context = {
                "overview": invalid_marker if reject_draft else "Keep originals.",
                **{field: [] for field in memory_summary.FIELDS},
            }
            context["constraints"] = [
                {"text": "Keep originals.", "refs": ["#999999" if reject_draft else "#0"]}
            ]
            value = {"summary": context["overview"], "context": context}
        else:
            assert "ops" in body["output_config"]["format"]["schema"]["properties"]
            value = {"ops": []}
        return Response(
            json.dumps({"content": [{"type": "text", "text": json.dumps(value)}]}).encode()
        )

    monkeypatch.setattr(api, "_open", respond)

    async def run():
        nonlocal reject_draft
        status, saved = await server.checkpoint_requests.request(
            "agent", {"request_id": "internal-api-good"}
        )
        assert status == 200 and saved["summary_update"]["state"] == "updated", saved
        revision_id = saved["record"]["summary_ref"]
        revision = server.digests.revision("agent", revision_id)
        assert revision["summary"] == "Keep originals."
        assert revision["continuity"]["summarized_records"] == 1
        assert revision["continuity"]["remaining_records"] == 0
        assert revision["continuity"]["context"]["constraints"][0]["text"] == "Keep originals."
        assert any(not request["writer"] for request in wire), "Real reconcile adapter was skipped"
        cache = server.history.session("agent")["progress"]
        count = len(wire)
        reused = await server.progress_coordinator.refresh_result("agent")
        assert reused.state == "reused" and reused.revision["id"] == revision_id
        assert len(wire) == count

        append(path, "A new instruction needs its own supported summary.")
        reject_draft = True
        status, failed = await server.checkpoint_requests.request(
            "agent", {"request_id": "internal-api-rejected"}
        )
        assert status == 200  # The checkpoint survives even when its new summary fails.
        update = failed["summary_update"]
        assert update["state"] == "failed" and update["reason"] == "invalid_context", update
        assert update["revision_id"] is None
        assert len(wire) == count + 2  # One invalid draft and its bounded format repair.
        assert failed["record"]["summary_ref"] == revision_id
        assert server.history.session("agent")["progress"] == cache
        assert server.digests.revision("agent", revision_id) == revision
        assert invalid_marker not in json.dumps(server.digests.items("agent"))
        assert invalid_marker not in json.dumps(server.history.checkpoints("agent"))

    asyncio.run(run())
    assert wire and all(request["body"]["model"] == "fixture-fast" for request in wire)
    assert all(request["url"] == "https://api.anthropic.com/v1/messages" for request in wire)
    assert all(request["headers"]["x-api-key"] == "fixture-internal-key" for request in wire)
    assert harness_calls == []
    assert checkpoint_settings.load()["writer_model"] == "opus"
    assert {key: server.history.session("agent")[key] for key in identity} == identity
    assert server.history.session_id_for("agent") == native_id == "agent-claude"
    assert server.history.restart_control("agent").get("native_binding") == native_binding
    assert server.history.session_api.authenticate(agent_headers) == "agent"
    assert all(path.read_bytes() == value for path, value in credentials.items())
    assert secrets == {"ai-anthropic": "fixture-internal-key"}
