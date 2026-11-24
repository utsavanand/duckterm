"""Real subprocess boundaries: final results, failure, bounded output and cleanup."""

import asyncio
import json
import os
import sys
import time
from pathlib import Path

import pytest

from duckterm import memory_provider
from duckterm.core.session_api import APIError
from duckterm.llm import latency


@pytest.fixture
def cli(monkeypatch):
    monkeypatch.delenv("DUCKTERM_SUMMARIZER", raising=False)
    processes = []
    create = asyncio.create_subprocess_exec

    async def capture(*args, **kwargs):
        proc = await create(*args, **kwargs)
        processes.append(proc)
        return proc

    monkeypatch.setattr(memory_provider.asyncio, "create_subprocess_exec", capture)

    def install(script):
        monkeypatch.setattr(
            memory_provider, "arguments", lambda *a, **kw: [sys.executable, "-u", "-c", script]
        )
        return processes

    return install


def emit(events, suffix=""):
    wire = b"".join(json.dumps(event).encode() + b"\n" for event in events)
    return (
        f"import sys, time\nsys.stdin.read()\nsys.stdout.buffer.write({wire!r})\n"
        f"sys.stdout.flush()\n{suffix}"
    )


def result(text="finished", **extra):
    return {"type": "result", "subtype": "success", "is_error": False, "result": text, **extra}


def call(schema=None):
    return asyncio.run(memory_provider.generate("claude-code", "haiku", "private prompt", schema))


def test_result_and_normal_process_exit_are_separately_timed(cli):
    processes = cli(emit([result()], "time.sleep(0.1)"))
    assert call() == "finished"
    assert processes[0].returncode == 0
    item = latency.recent()[-1]
    assert item["status"] == "ok"
    marks = item["marks"]
    assert marks["result_ms"] <= marks["reaped_ms"] <= marks["total_ms"]
    assert marks["reaped_ms"] - marks["result_ms"] >= 80
    assert "first_text_ms" not in marks  # metadata is not a token


def test_partial_utf8_lines_and_first_text_are_not_final_result(cli):
    events = [
        {"type": "system", "subtype": "init"},
        {
            "type": "stream_event",
            "event": {"delta": {"type": "thinking_delta", "thinking": "private reasoning"}},
        },
        {"type": "stream_event", "event": {"delta": {"type": "text_delta", "text": "draft é"}}},
        result("final é"),
    ]
    wire = "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in events).encode()
    cli(
        f"import sys\nsys.stdin.read()\nfor b in {wire!r}:\n"
        " sys.stdout.buffer.write(bytes([b])); sys.stdout.flush()"
    )
    assert call() == "final é"
    item = latency.recent()[-1]
    assert (
        item["marks"]["first_stdout_ms"]
        <= item["marks"]["first_text_ms"]
        <= item["marks"]["result_ms"]
    )
    assert item["output_bytes"] == len("final é".encode())
    assert "private" not in json.dumps(item) and "draft" not in json.dumps(item)


@pytest.mark.parametrize("schema", [None, {"type": "object"}])
def test_structured_output_and_refusal_fallback_are_preserved(cli, schema):
    cli(emit([result("text", structured_output={"overview": "complete"})]))
    value = call(schema)
    assert (json.loads(value) if schema else value) == (
        {"overview": "complete"} if schema else "text"
    )
    cli(emit([result("declined")]))
    assert call(schema) == "declined"


@pytest.mark.parametrize(
    "events",
    [
        [],
        [{"type": "assistant", "message": {"content": [{"text": "not final"}]}}],
        [result(is_error=True)],
        [result(subtype="error_max_turns")],
        [result(text={"invalid": "shape"})],
        [["invalid event shape"]],
    ],
)
def test_partial_or_failed_response_is_never_success(cli, events):
    cli(emit(events))
    with pytest.raises(APIError):
        call()
    assert latency.recent()[-1]["status"] == "failed"


@pytest.mark.parametrize(
    "script",
    [
        "import sys; sys.stdin.read(); print('not json')",
        "import sys; sys.stdin.read(); sys.stdout.buffer.write(b'\\xff\\n')",
    ],
)
def test_malformed_output_is_a_provider_error(cli, script):
    cli(script)
    with pytest.raises(APIError, match="complete response"):
        call()


def test_known_nonzero_exit_is_not_success(cli):
    cli(emit([result()], "time.sleep(0.1); sys.exit(2)"))
    with pytest.raises(APIError):
        call()


@pytest.mark.parametrize("kind", ["final", "delta", "record", "wire"])
def test_output_bounds_stop_and_reap_the_child(cli, monkeypatch, kind):
    if kind == "final":
        events = [result("x" * (memory_provider.MAX_REPLY + 1))]
    elif kind == "delta":
        events = [
            {
                "type": "stream_event",
                "event": {
                    "delta": {"type": "text_delta", "text": "x" * (memory_provider.MAX_REPLY + 1)}
                },
            }
        ]
    elif kind == "record":
        events = [{"type": "system", "padding": "x" * memory_provider.MAX_STREAM_RECORD}]
    else:
        monkeypatch.setattr(memory_provider, "MAX_STREAM_BYTES", 100)
        events = [{"type": "system", "padding": "x" * 30}] * 5
    processes = cli(emit(events, "time.sleep(30)"))
    with pytest.raises(APIError, match="too much output"):
        call()
    assert processes[0].returncode not in (None, 0)


def test_timeout_and_cancellation_are_recorded_and_reaped(cli, monkeypatch):
    processes = cli("import sys, time; sys.stdin.read(); time.sleep(30)")
    monkeypatch.setattr(memory_provider, "CALL_TIMEOUT", 0.05)
    with pytest.raises(memory_provider.BatchTimeout):
        call()
    assert latency.recent()[-1]["status"] == "timeout"
    assert processes[0].returncode is not None
    monkeypatch.setattr(memory_provider, "CALL_TIMEOUT", 30)

    async def cancel():
        task = asyncio.create_task(memory_provider.generate("claude-code", "haiku", "p"))
        await asyncio.sleep(0.1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(cancel())
    assert processes[-1].returncode is not None
    assert latency.recent()[-1]["status"] == "cancelled"


@pytest.mark.skipif(os.name == "nt", reason="POSIX process group")
def test_failed_job_reaps_descendants(cli, tmp_path: Path):
    marker = tmp_path / "descendant-ran"
    descendant = (
        f"import time; from pathlib import Path; time.sleep(0.6); Path({str(marker)!r}).touch()"
    )
    setup = f"import subprocess\nsubprocess.Popen([sys.executable, '-c', {descendant!r}])\n"
    cli("import sys, time\n" + setup + emit([result(is_error=True)], "time.sleep(30)"))
    with pytest.raises(APIError):
        call()
    time.sleep(0.8)
    assert not marker.exists()


def test_diagnostics_are_bounded_redacted_and_independent():
    for _ in range(70):
        with latency.measure("harness", model="private-model-id", backend="private-url"):
            pass
    records = latency.recent()
    assert len(records) == 64
    assert "private" not in json.dumps(records)
    records[-1]["marks"]["total_ms"] = -1
    assert latency.recent()[-1]["marks"]["total_ms"] >= 0
    with latency.measure("backend") as outer, latency.measure("harness"):
        pass
    assert latency.recent()[-2]["parent_id"] == outer.call_id
    assert latency.recent()[-1]["parent_id"] == ""


@pytest.mark.parametrize("harness", ["claude-code", "codex"])
def test_background_traffic_is_disabled_only_for_internal_claude(monkeypatch, harness):
    monkeypatch.delenv("DUCKTERM_SUMMARIZER", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fixture-secret")
    monkeypatch.setenv("DUCKTERM_SESSION_TOKEN", "fixture-session-secret")
    seen = {}

    async def spawn(*args, **kwargs):
        seen.update(kwargs["env"])
        raise OSError("fixture")

    monkeypatch.setattr(memory_provider.asyncio, "create_subprocess_exec", spawn)
    with pytest.raises(APIError):
        asyncio.run(memory_provider.generate(harness, "haiku", "p"))
    assert seen.get("CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC") == (
        "1" if harness == "claude-code" else None
    )
    assert "ANTHROPIC_API_KEY" not in seen and "DUCKTERM_SESSION_TOKEN" not in seen
    assert seen["DUCKTERM_INTERNAL"] == "1"


def test_duplicate_result_cannot_replace_validated_answer(cli):
    cli(emit([result("first"), result("second")]))
    with pytest.raises(APIError, match="multiple final"):
        call()


def test_streaming_arguments_preserve_schema_and_isolation(tmp_path):
    schema = tmp_path / "schema.json"
    schema.write_text('{"type":"object"}')
    args = memory_provider.arguments("claude-code", "haiku", schema, stream=True, effort="low")
    assert args.count("--output-format") == 1
    assert args[args.index("--output-format") + 1] == "stream-json"
    assert {"--verbose", "--include-partial-messages", "--no-session-persistence"} <= set(args)
    assert args[args.index("--tools") + 1] == ""
    assert args[args.index("--json-schema") + 1] == '{"type":"object"}'
    assert args[args.index("--effort") + 1] == "low"
    assert "--bare" not in args


def test_large_result_line_preserves_utf8_answer_under_reply_limit(cli):
    text = "é" * 20000
    # Escaped JSON is over the default 64-KiB asyncio line limit, while the
    # decoded answer is only 40,000 bytes and must still be accepted.
    cli(emit([result(text)]))
    assert call() == text
    assert latency.recent()[-1]["output_bytes"] == 40000
