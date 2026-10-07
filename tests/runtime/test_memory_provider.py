"""Exercise the preparation CLI boundary without a provider account."""

import asyncio
import json
import os
import sys
from pathlib import Path

import pytest

from duckterm import memory_provider, memory_summary
from duckterm.core.session_api import APIError


def test_codex_schema_argument_keeps_selected_model_and_isolation():
    args = memory_provider.arguments("codex", "chosen-model", Path("/tmp/schema.json"))
    assert args[args.index("--output-schema") + 1] == "/tmp/schema.json"
    assert args[args.index("--model") + 1] == "chosen-model"
    assert args[args.index("--sandbox") + 1] == "read-only"
    assert {"--ephemeral", "--ignore-user-config", "--ignore-rules"} <= set(args)
    assert "features.shell_tool=false" in args
    assert args[-1] == "-"


def test_response_schema_is_private_and_removed_after_call(monkeypatch):
    script = """
import json, os, pathlib, stat, sys
path = pathlib.Path(sys.argv[1])
print(json.dumps({
    'schema': json.loads(path.read_text()),
    'directory': str(path.parent),
    'mode': stat.S_IMODE(path.parent.stat().st_mode),
    'prompt': sys.stdin.read(),
    'capability_removed': 'DUCKTERM_SESSION_TOKEN' not in os.environ,
    'internal': os.environ.get('DUCKTERM_INTERNAL'),
}))
"""
    monkeypatch.delenv("DUCKTERM_SUMMARIZER", raising=False)
    monkeypatch.setenv("DUCKTERM_SESSION_TOKEN", "fixture-not-a-credential")
    monkeypatch.setattr(
        memory_provider,
        "arguments",
        lambda harness, model, path: [sys.executable, "-c", script, str(path)],
    )
    reply = asyncio.run(
        memory_provider.generate("codex", "chosen-model", "fixture", memory_summary.SUMMARY_SCHEMA)
    )
    value = json.loads(reply)
    assert value["schema"]["properties"]["overview"] == {"type": "string"}
    assert value["schema"]["additionalProperties"] is False
    assert value["mode"] == 0o700
    assert value["prompt"] == "fixture"
    assert value["capability_removed"] and value["internal"] == "1"
    assert not Path(value["directory"]).exists()


@pytest.mark.skipif(os.name == "nt", reason="POSIX preparation process group cleanup")
def test_timeout_has_specific_error_and_reaps_only_owned_process(monkeypatch):
    process = None
    create = asyncio.create_subprocess_exec

    async def capture(*args, **kwargs):
        nonlocal process
        process = await create(*args, **kwargs)
        return process

    monkeypatch.delenv("DUCKTERM_SUMMARIZER", raising=False)
    monkeypatch.setattr(memory_provider.asyncio, "create_subprocess_exec", capture)
    monkeypatch.setattr(memory_provider, "CALL_TIMEOUT", 0.1)
    monkeypatch.setattr(
        memory_provider,
        "arguments",
        lambda *args: [sys.executable, "-c", "import time; time.sleep(30)"],
    )
    with pytest.raises(APIError, match="took too long.*history batch"):
        asyncio.run(memory_provider.generate("codex", "chosen-model", "fixture"))
    assert process is not None and process.returncode is not None
    with pytest.raises(ProcessLookupError):
        os.kill(process.pid, 0)


def test_summary_and_review_request_distinct_response_contracts(monkeypatch):
    schemas = []

    async def provider(harness, model, prompt, schema):
        schemas.append(schema)
        assert (harness, model) == ("codex", "chosen-model")
        if schema == memory_summary.VERDICT_SCHEMA:
            return '{"ready":true,"reason_codes":[]}'
        return json.dumps({"overview": "Work remains.", **{k: [] for k in memory_summary.FIELDS}})

    monkeypatch.setattr(memory_provider, "generate", provider)
    asyncio.run(
        memory_summary.summarize(
            [
                {
                    "id": "original",
                    "kind": "conversation",
                    "records": [
                        {"id": "0", "role": "user", "text": "Continue the unfinished work."}
                    ],
                }
            ],
            {"harness": "codex", "model": {"mode": "explicit", "id": "chosen-model"}},
        )
    )
    assert schemas == [memory_summary.SUMMARY_SCHEMA, memory_summary.VERDICT_SCHEMA]


@pytest.mark.parametrize("overview", [[{"text": "Wrong shape", "refs": ["s:r"]}], "é" * 401])
def test_schema_does_not_replace_semantic_or_utf8_validation(overview):
    value = {"overview": overview, **{k: [] for k in memory_summary.FIELDS}}
    with pytest.raises(APIError, match="invalid or oversized"):
        memory_summary.parse(json.dumps(value), {"s:r"})
    value["overview"] = "Summary"
    value["constraints"] = [{"text": "Keep source.", "refs": ["invented:source"]}]
    with pytest.raises(APIError, match="invalid or oversized"):
        memory_summary.parse(json.dumps(value), {"s:r"})


def test_content_repair_is_reviewed_again_and_only_then_counted(monkeypatch):
    calls, progress, checks = [], [], []
    initial = {"overview": "Work remains.", **{k: [] for k in memory_summary.FIELDS}}
    corrected = {**initial, "constraints": [{"text": "Keep originals.", "refs": ["s:r"]}]}
    replies = iter(
        [
            json.dumps(initial),
            '{"ready":false,"reason_codes":["Missing owner constraint: keep originals"]}',
            json.dumps(corrected),
            '{"ready":true,"reason_codes":[]}',
        ]
    )

    async def provider(harness, model, prompt, schema):
        calls.append((prompt, schema))
        return next(replies)

    monkeypatch.setattr(memory_provider, "generate", provider)
    sources = [
        {
            "id": "s",
            "kind": "conversation",
            "records": [{"id": "r", "role": "user", "text": "Keep originals."}],
        }
    ]
    context, _ = asyncio.run(
        memory_summary.summarize(
            sources,
            {"harness": "codex", "model": {"mode": "default"}},
            check=lambda: checks.append(True),
            progress=lambda *args: progress.append(args),
        )
    )
    assert context == corrected
    assert len(calls) == 4 and len(checks) == 6
    assert "Missing owner constraint" in calls[2][0]
    assert '"candidate":' in calls[3][0] and "Keep originals." in calls[3][0]
    assert progress == [
        (0, 1, "summarizing"),
        (0, 1, "reviewing"),
        (0, 1, "repairing"),
        (0, 1, "reviewing"),
        (1, 1, "complete"),
    ]


def test_repair_exhaustion_cannot_promote_a_summary(monkeypatch):
    calls = []

    async def provider(harness, model, prompt, schema):
        calls.append(schema)
        if schema == memory_summary.VERDICT_SCHEMA:
            return '{"ready":false,"reason_codes":["Missing owner constraint"]}'
        return json.dumps({"overview": "Incomplete.", **{k: [] for k in memory_summary.FIELDS}})

    monkeypatch.setattr(memory_provider, "generate", provider)
    with pytest.raises(APIError, match="could not preserve"):
        asyncio.run(memory_summary.prepare_group("Source", "codex", "selected", set(), None))
    assert len(calls) == 6


def test_changed_sources_stop_before_using_repair_feedback(monkeypatch):
    calls = []

    async def provider(*args):
        calls.append(args)
        return "malformed draft"

    def changed():
        raise APIError(409, "Sources changed")

    monkeypatch.setattr(memory_provider, "generate", provider)
    with pytest.raises(APIError, match="Sources changed"):
        asyncio.run(memory_summary.prepare_group("Source", "codex", "selected", set(), changed))
    assert len(calls) == 0
