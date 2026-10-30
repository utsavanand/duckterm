"""DuckTerm's AI features reach a model through one of three backends: the
owner's harness CLI login, the owner's API key, or DuckTerm's gateway (not yet
available). These tests pin the wire format sent to providers, the fallback to
the harness, and that a saved key is never written to settings or echoed."""

import asyncio
import http.server
import io
import json
import threading
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

    monkeypatch.setattr(api, "_open", urlopen)
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
        {"base_url": "https://collector.example/v1"},  # anthropic has its own address
        {"provider": "custom", "base_url": "http://gateway.example/v1"},  # key in clear text
        {"provider": "custom", "base_url": "file:///etc/passwd"},
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


def test_switching_away_from_custom_drops_its_base_url(memory_rig, provider, monkeypatch):
    """An owner who once tried a gateway and then picks Anthropic must not send
    the Anthropic key to that gateway."""
    sent, replies = provider
    _harness_fake(monkeypatch, [])
    ai_settings.save_key("anthropic", "sk-stored")
    ai_settings.save(
        {
            "backend": "api",
            "provider": "custom",
            "base_url": "https://gateway.example/v1",
            "fast_model": "m",
            "deep_model": "m",
        }
    )
    saved = ai_settings.save({"provider": "anthropic", "fast_model": "", "deep_model": ""})
    assert saved["base_url"] == ""
    replies.append({"content": [{"type": "text", "text": "ok"}]})
    asyncio.run(backend.complete("p", tier="fast"))
    assert sent[-1]["url"] == "https://api.anthropic.com/v1/messages"


def test_a_named_provider_refuses_a_base_url(memory_rig, provider):
    server, _, _, _ = memory_rig
    sent, _ = provider
    with pytest.raises(APIError):
        ai_settings.save({"backend": "api", "base_url": "http://attacker.example"})
    body = json.dumps(
        {"provider": "anthropic", "key": "sk-new", "base_url": "http://attacker.example"}
    ).encode()
    status, _ = dispatch(
        server, "POST", "/ai-settings/key", {"x-duckterm-token": server.token}, body
    )
    assert status == 400 and sent == []


def test_a_provider_redirect_does_not_carry_the_key(memory_rig):
    """urllib copies key headers onto a redirect, even to another host."""
    received: list[dict] = []

    class Collector(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            received.append(dict(self.headers))
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"data": []}')

        def log_message(self, *args):
            pass

    collector = http.server.HTTPServer(("127.0.0.1", 0), Collector)
    target = f"http://127.0.0.1:{collector.server_port}/v1/models"

    class Redirector(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(302)
            self.send_header("Location", target)
            self.end_headers()

        def log_message(self, *args):
            pass

    redirector = http.server.HTTPServer(("127.0.0.1", 0), Redirector)
    servers = [collector, redirector]
    for server in servers:
        threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        base = f"http://127.0.0.1:{redirector.server_port}/v1"
        for wire in ("anthropic", "openai"):
            with pytest.raises(APIError, match="redirected"):
                api.models(wire, base, "sk-CANARY")
    finally:
        for server in servers:
            server.shutdown()
            server.server_close()
    assert received == []


def test_a_provider_error_never_repeats_the_key(memory_rig, provider):
    server, _, _, _ = memory_rig
    _, replies = provider
    secret = "sk-synthetic-echo"
    echo = json.dumps({"error": {"message": f"invalid key {secret}"}}).encode()
    replies.append(urllib.error.HTTPError("u", 401, "no", {}, io.BytesIO(echo)))  # type: ignore[arg-type]
    replies.append(urllib.error.URLError(f"refused for {secret}"))
    owner = {"x-duckterm-token": server.token}
    for provider_name in ("anthropic", "openai"):
        body = json.dumps({"provider": provider_name, "key": secret}).encode()
        status, result = dispatch(server, "POST", "/ai-settings/key", owner, body)
        assert status in (502, 503) and secret not in json.dumps(result)
        assert "[key]" in result["error"]


def test_the_harness_never_sees_a_provider_key(monkeypatch):
    """A key in the server's environment would let the CLI switch from the
    owner's login to per-token billing."""
    monkeypatch.delenv("DUCKTERM_SUMMARIZER", raising=False)
    for env in ai_settings.env_keys():
        monkeypatch.setenv(env, "sk-synthetic-env")
    captured: dict = {}

    async def spawn(*args, **kwargs):
        captured.update(kwargs["env"])
        raise OSError("stop before spawning")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    with pytest.raises(APIError):
        asyncio.run(memory_provider.generate("claude-code", "haiku", "p"))
    assert captured and not ai_settings.env_keys() & set(captured)


def test_a_custom_key_only_goes_to_the_url_it_was_verified_for(memory_rig, provider, monkeypatch):
    server, _, _, _ = memory_rig
    sent, replies = provider
    calls: list = []
    _harness_fake(monkeypatch, calls)
    owner = {"x-duckterm-token": server.token}
    body = json.dumps(
        {"provider": "custom", "key": "sk-local", "base_url": "http://127.0.0.1:11434/v1"}
    ).encode()
    replies.append({"data": [{"id": "llama"}]})
    assert dispatch(server, "POST", "/ai-settings/key", owner, body)[0] == 200
    settings = {
        "backend": "api",
        "provider": "custom",
        "fast_model": "llama",
        "deep_model": "llama",
    }
    ai_settings.save({**settings, "base_url": "http://127.0.0.1:11434/v1"})
    replies.append({"choices": [{"message": {"content": "ok"}}]})
    asyncio.run(backend.complete("p", tier="fast"))
    assert sent[-1]["headers"]["authorization"] == "Bearer sk-local"

    ai_settings.save({**settings, "base_url": "https://collector.example/v1"})
    replies.append({"choices": [{"message": {"content": "ok"}}]})
    asyncio.run(backend.complete("p", tier="fast"))
    assert sent[-1]["url"] == "https://collector.example/v1/chat/completions"
    assert "authorization" not in sent[-1]["headers"]
    _, state = dispatch(server, "GET", "/ai-settings", owner)
    assert state["keys"]["custom"]["saved"] is False


def test_codex_fast_tier_uses_luna_only_when_the_account_lists_it(
    memory_rig, monkeypatch, tmp_path
):
    monkeypatch.delenv("DUCKTERM_SUMMARIZER", raising=False)
    monkeypatch.setattr(
        backend.shutil, "which", lambda b: "/fixture/codex" if b == "codex" else None
    )
    monkeypatch.setattr(backend.Path, "home", lambda: tmp_path)
    calls: list = []

    async def generate(name, model, prompt, schema, **options):
        calls.append((name, model, options["effort"]))
        return "ok"

    monkeypatch.setattr(memory_provider, "generate", generate)
    # No models cache (or an account without Luna): Codex's default model.
    reply = asyncio.run(backend.complete("p", tier="fast"))
    assert calls[-1] == ("codex", "", "low") and reply.detail == "codex"
    (tmp_path / ".codex").mkdir()
    (tmp_path / ".codex" / "models_cache.json").write_text(
        json.dumps({"models": [{"slug": "gpt-6-astra"}, {"slug": "gpt-6-luna"}]})
    )
    reply = asyncio.run(backend.complete("p", tier="fast"))
    assert calls[-1] == ("codex", "gpt-6-luna", "low") and reply.detail == "codex/gpt-6-luna"
    asyncio.run(backend.complete("p", tier="deep"))
    assert calls[-1] == ("codex", "", "medium")


def test_each_call_logs_feature_time_and_what_served_it(memory_rig, provider, monkeypatch, capsys):
    calls: list = []
    _harness_fake(monkeypatch, calls)
    ai_settings.save({"backend": "api"})
    asyncio.run(backend.complete("p" * 3000, tier="deep", feature="oracle"))
    line = capsys.readouterr().err.strip().splitlines()[-1]
    assert line.startswith("[duckterm] ai oracle deep ")
    assert "in=3KB harness claude-code/sonnet (fallback: No anthropic API key" in line

    async def broken(*_a, **_kw):
        raise APIError(503, "usage limit")

    monkeypatch.setattr(memory_provider, "generate", broken)
    with pytest.raises(APIError):
        asyncio.run(backend.complete("p", tier="fast", feature="compaction"))
    assert capsys.readouterr().err.strip().endswith("failed: usage limit")


def _sse(events: list[object]) -> _Response:
    lines = [f"data: {json.dumps(e) if not isinstance(e, str) else e}\n\n" for e in events]
    return _Response("".join(lines).encode())


def test_api_streams_pass_text_deltas_through(monkeypatch):
    replies = [
        _sse(
            [
                {"type": "message_start"},
                {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "Two "}},
                {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "wait."}},
                {"type": "message_stop"},
            ]
        ),
        _sse([{"choices": [{"delta": {"content": "Hi"}}]}, {"choices": [{"delta": {}}]}, "[DONE]"]),
        _sse([{"type": "message_delta", "delta": {"stop_reason": "refusal"}}]),
        _sse([{"type": "error", "error": {"message": "overloaded for sk-echoed"}}]),
    ]
    sent: list[dict] = []

    def urlopen(req, timeout):
        sent.append(json.loads(req.data))
        return replies.pop(0)

    monkeypatch.setattr(api, "_open", urlopen)
    assert list(api.anthropic_stream("https://p", "k", "claude-haiku-5-5", "s", "p", "low")) == [
        "Two ",
        "wait.",
    ]
    assert sent[0]["stream"] is True and "output_config" in sent[0]
    assert list(api.openai_chat_stream("https://p", None, "m", "s", "p")) == ["Hi"]
    with pytest.raises(APIError, match="declined"):
        list(api.anthropic_stream("https://p", "k", "m", "s", "p", "low"))
    with pytest.raises(APIError, match="overloaded") as caught:
        list(api.anthropic_stream("https://p", "sk-echoed", "m", "s", "p", "low"))
    assert "sk-echoed" not in str(caught.value)


def test_claude_stream_yields_partial_text_from_the_cli(tmp_path, monkeypatch):
    fake = tmp_path / "bin" / "claude"
    fake.parent.mkdir()
    events = [
        {"type": "system", "subtype": "init"},
        {"type": "stream_event", "event": {"type": "message_start"}},
        {
            "type": "stream_event",
            "event": {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "A"}},
        },
        {
            "type": "stream_event",
            "event": {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "B"}},
        },
        {"type": "result", "is_error": False, "result": "AB"},
    ]
    script = "\n".join(f"echo '{json.dumps(e)}'" for e in events)
    fake.write_text(f"#!/bin/sh\ncat > /dev/null\n{script}\n")
    fake.chmod(0o755)
    monkeypatch.setenv("PATH", f"{fake.parent}:/usr/bin:/bin")
    monkeypatch.delenv("DUCKTERM_SUMMARIZER", raising=False)

    async def collect() -> list[str]:
        return [piece async for piece in memory_provider.stream("haiku", "p", effort="low")]

    assert asyncio.run(collect()) == ["A", "B"]
    failing = json.dumps({"type": "result", "is_error": True, "result": "limit"})
    fake.write_text(f"#!/bin/sh\ncat > /dev/null\necho '{failing}'\n")
    with pytest.raises(APIError, match="usage limit"):
        asyncio.run(collect())


def test_stream_falls_back_only_before_the_first_words(memory_rig, provider, monkeypatch):
    _, replies = provider
    monkeypatch.delenv("DUCKTERM_SUMMARIZER", raising=False)
    monkeypatch.setattr(
        backend.shutil, "which", lambda b: "/fixture/claude" if b == "claude" else None
    )

    async def harness_stream(model, prompt, *, effort=""):
        yield "from harness"

    monkeypatch.setattr(memory_provider, "stream", harness_stream)
    ai_settings.save({"backend": "api"})
    ai_settings.save_key("anthropic", "sk-expired")

    async def collect() -> list[str]:
        return [piece async for piece in backend.stream("p", tier="deep", feature="oracle")]

    # The key fails before any words: the owner's login answers instead.
    replies.append(urllib.error.HTTPError("u", 429, "Too Many", {}, io.BytesIO(b"{}")))  # type: ignore[arg-type]
    assert asyncio.run(collect()) == ["from harness"]

    # The key fails after some words: the error reaches the caller.
    def partial(*_a, **_kw):
        yield "half an "
        raise APIError(503, "Provider could not be reached: reset")

    monkeypatch.setattr(api, "anthropic_stream", partial)
    shown: list[str] = []

    async def until_failure() -> None:
        async for piece in backend.stream("p", tier="deep"):
            shown.append(piece)

    with pytest.raises(APIError, match="reset"):
        asyncio.run(until_failure())
    assert shown == ["half an "]
