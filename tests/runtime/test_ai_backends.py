"""DuckTerm's AI features reach a model through one of three backends: the
owner's harness CLI login, the owner's API key, or DuckTerm's gateway (not yet
available). These tests pin the wire format sent to providers, the fallback to
the harness, and that a saved key is never written to settings or echoed."""

import asyncio
import io
import json
import urllib.error
from pathlib import Path

import pytest
from test_memory import memory_rig
from test_session_api import dispatch

from duckterm import memory_provider
from duckterm.core.session_api import APIError
from duckterm.llm import ai_settings, api, backend

__all__ = ["memory_rig"]


class _Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.fixture
def provider(monkeypatch):
    """A fake provider endpoint: records each request, answers from `replies`."""
    sent: list[dict] = []
    replies: list[object] = []

    def urlopen(req, timeout):
        sent.append(
            {
                "url": req.full_url,
                "method": req.get_method(),
                "headers": {k.lower(): v for k, v in req.header_items()},
                "body": json.loads(req.data) if req.data else None,
            }
        )
        reply = replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return _Response(json.dumps(reply).encode())

    monkeypatch.setattr(api.urllib.request, "urlopen", urlopen)
    monkeypatch.setenv("DUCKTERM_NO_KEYCHAIN", "1")
    for env in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "OPENROUTER_API_KEY"):
        monkeypatch.delenv(env, raising=False)
    return sent, replies


SCHEMA = {
    "type": "object",
    "required": ["kind"],
    "properties": {
        "kind": {"type": "string"},
        "items": {"type": "array", "items": {"type": "object", "properties": {}}},
    },
}


def test_anthropic_request_enforces_schema_and_effort(provider):
    sent, replies = provider
    replies.append({"stop_reason": "end_turn", "content": [{"type": "text", "text": '{"kind":1}'}]})
    text = api.anthropic(
        "https://api.anthropic.com/v1", "sk-test", "claude-haiku-5-5", "sys", "hi", SCHEMA, "low"
    )
    assert text == '{"kind":1}'
    request = sent[0]
    assert request["url"] == "https://api.anthropic.com/v1/messages"
    assert request["headers"]["x-api-key"] == "sk-test"
    assert request["headers"]["anthropic-version"] == "2023-06-01"
    output = request["body"]["output_config"]
    assert output["effort"] == "low"
    assert output["format"]["type"] == "json_schema"
    closed = output["format"]["schema"]
    assert closed["additionalProperties"] is False
    assert closed["properties"]["items"]["items"]["additionalProperties"] is False
    assert "additionalProperties" not in SCHEMA  # the caller's schema is not mutated


def test_untested_anthropic_model_gets_no_effort(provider):
    sent, replies = provider
    replies.append({"content": [{"type": "text", "text": "ok"}]})
    api.anthropic("https://x/v1", "k", "claude-haiku-4-5", "sys", "hi", None, "low")
    assert "output_config" not in sent[0]["body"]


def test_refusal_is_an_error_not_an_empty_answer(provider):
    _, replies = provider
    replies.append({"stop_reason": "refusal", "content": []})
    with pytest.raises(APIError, match="declined"):
        api.anthropic("https://x/v1", "k", "claude-sonnet-5-5", "s", "p", None, "medium")


def test_openai_compatible_request_shape(provider):
    sent, replies = provider
    replies.append({"choices": [{"message": {"content": "answer"}}]})
    text = api.openai_chat("http://localhost:11434/v1", None, "llama", "sys", "hi", SCHEMA)
    assert text == "answer"
    request = sent[0]
    assert request["url"] == "http://localhost:11434/v1/chat/completions"
    assert "authorization" not in request["headers"]  # local server, no key
    assert request["body"]["messages"][0] == {"role": "system", "content": "sys"}
    assert request["body"]["response_format"]["json_schema"]["schema"] == SCHEMA


def test_provider_error_names_the_problem_without_the_key(provider):
    _, replies = provider
    body = io.BytesIO(json.dumps({"error": {"message": "invalid x-api-key"}}).encode())
    replies.append(urllib.error.HTTPError("u", 401, "Unauthorized", {}, body))  # type: ignore[arg-type]
    with pytest.raises(APIError) as caught:
        api.models("anthropic", "https://x/v1", "sk-secret-value")
    assert "401" in str(caught.value) and "invalid x-api-key" in str(caught.value)
    assert "sk-secret-value" not in str(caught.value)


@pytest.mark.parametrize(
    "value",
    [
        {"backend": "cloud"},
        {"harness": "copilot"},
        {"provider": "custom"},  # no base URL
        {"base_url": "file:///etc/passwd"},
        {"backend": "api", "provider": "openrouter"},  # no models chosen
        {"unexpected": "x"},
    ],
)
def test_invalid_settings_are_rejected(memory_rig, value):
    with pytest.raises(APIError):
        ai_settings.save(value)
    assert ai_settings.load() == ai_settings.DEFAULTS


def _harness_fake(monkeypatch, calls):
    monkeypatch.delenv("DUCKTERM_SUMMARIZER", raising=False)
    monkeypatch.setattr(
        backend.shutil, "which", lambda b: "/fixture/claude" if b == "claude" else None
    )

    async def generate(name, model, prompt, schema, **options):
        calls.append((name, model, options["effort"]))
        return "from harness"

    monkeypatch.setattr(memory_provider, "generate", generate)


def test_api_backend_serves_with_tier_model(memory_rig, provider, monkeypatch):
    sent, replies = provider
    calls: list = []
    _harness_fake(monkeypatch, calls)
    ai_settings.save({"backend": "api"})
    ai_settings.save_key("anthropic", "sk-stored")
    replies.append({"content": [{"type": "text", "text": "from api"}]})
    reply = asyncio.run(backend.complete("p", tier="fast"))
    assert (reply.text, reply.backend, reply.detail) == (
        "from api",
        "api",
        "anthropic/claude-haiku-5-5",
    )
    assert sent[0]["body"]["model"] == "claude-haiku-5-5"
    assert sent[0]["headers"]["x-api-key"] == "sk-stored"
    assert calls == []


def test_failing_key_falls_back_to_the_harness(memory_rig, provider, monkeypatch):
    _, replies = provider
    calls: list = []
    _harness_fake(monkeypatch, calls)
    ai_settings.save({"backend": "api"})
    ai_settings.save_key("anthropic", "sk-expired")
    replies.append(urllib.error.HTTPError("u", 429, "Too Many", {}, io.BytesIO(b"{}")))  # type: ignore[arg-type]
    reply = asyncio.run(backend.complete("p", tier="deep"))
    assert (reply.text, reply.backend) == ("from harness", "harness")
    assert "429" in reply.fallback
    assert calls == [("claude-code", "sonnet", "medium")]


def test_api_backend_without_a_key_uses_the_harness(memory_rig, provider, monkeypatch):
    calls: list = []
    _harness_fake(monkeypatch, calls)
    ai_settings.save({"backend": "api"})
    reply = asyncio.run(backend.complete("p", tier="fast"))
    assert reply.backend == "harness" and "No anthropic API key" in reply.fallback
    assert calls == [("claude-code", "haiku", "low")]


def test_duckterm_gateway_is_a_stub_that_falls_back(memory_rig, monkeypatch):
    calls: list = []
    _harness_fake(monkeypatch, calls)
    ai_settings.save({"backend": "duckterm"})
    reply = asyncio.run(backend.complete("p", tier="fast"))
    assert reply.backend == "harness" and "not available yet" in reply.fallback


def test_claude_schema_call_reads_structured_output():
    envelope = json.dumps({"result": "text", "structured_output": {"kind": "blocked"}})
    assert json.loads(memory_provider._structured(envelope)) == {"kind": "blocked"}
    # No structured output (e.g. a declined request): the text goes to the caller's repair path.
    assert memory_provider._structured(json.dumps({"result": "plain"})) == "plain"
    with pytest.raises(APIError):
        memory_provider._structured(json.dumps({"is_error": True, "result": "limit"}))


def test_claude_arguments_carry_effort_and_schema(tmp_path: Path):
    schema = tmp_path / "schema.json"
    schema.write_text(json.dumps(SCHEMA))
    args = memory_provider.arguments("claude-code", "haiku", schema, effort="low")
    assert args[args.index("--effort") + 1] == "low"
    assert args[args.index("--output-format") + 1] == "json"
    assert json.loads(args[args.index("--json-schema") + 1]) == SCHEMA
    codex = memory_provider.arguments("codex", "", None, effort="low")
    assert 'model_reasoning_effort="low"' in codex


def test_key_route_validates_saves_and_never_echoes(memory_rig, provider):
    server, agent, _, _ = memory_rig
    sent, replies = provider
    owner = {"x-duckterm-token": server.token}
    body = json.dumps({"provider": "anthropic", "key": "sk-new"}).encode()
    assert dispatch(server, "POST", "/ai-settings/key", agent, body)[0] == 403
    assert dispatch(server, "GET", "/ai-settings", agent)[0] == 403

    replies.append({"data": [{"id": "claude-sonnet-5-5"}, {"id": "claude-haiku-5-5"}]})
    status, result = dispatch(server, "POST", "/ai-settings/key", owner, body)
    assert status == 200
    assert result == {
        "provider": "anthropic",
        "saved": True,
        "models": ["claude-haiku-5-5", "claude-sonnet-5-5"],
    }
    assert sent[0]["url"] == "https://api.anthropic.com/v1/models?limit=1000"

    status, state = dispatch(server, "GET", "/ai-settings", owner)
    assert status == 200 and state["keys"]["anthropic"] == {"saved": True, "source": "stored"}
    assert "sk-new" not in json.dumps(state)
    assert "sk-new" not in (
        Path(ai_settings._path()).read_text() if ai_settings._path().exists() else ""
    )

    assert (
        dispatch(server, "DELETE", "/ai-settings/key", owner, b'{"provider":"anthropic"}')[0] == 200
    )
    assert dispatch(server, "GET", "/ai-settings", owner)[1]["keys"]["anthropic"]["saved"] is False


def test_rejected_key_is_not_saved(memory_rig, provider):
    server, _, _, _ = memory_rig
    _, replies = provider
    replies.append(urllib.error.HTTPError("u", 401, "no", {}, io.BytesIO(b"{}")))  # type: ignore[arg-type]
    owner = {"x-duckterm-token": server.token}
    body = json.dumps({"provider": "anthropic", "key": "sk-bad"}).encode()
    status, result = dispatch(server, "POST", "/ai-settings/key", owner, body)
    assert status == 502 and "401" in result["error"]
    assert ai_settings.key("anthropic") == (None, "")
