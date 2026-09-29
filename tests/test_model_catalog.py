import asyncio
import json
import sys
from pathlib import Path

import pytest

from duckterm import model_catalog as catalog


def test_specific_claude_versions_keep_context_and_dedupe_aliases():
    rows = [
        {"value": "default", "resolvedModel": "claude-opus-5-5"},
        {"value": "opus", "resolvedModel": "claude-opus-5-5"},
        {"value": "claude-fable-5-1[1m]", "resolvedModel": "claude-fable-5-1"},
        {"value": "haiku", "resolvedModel": "claude-haiku-4-5-20251001"},
        {"value": "sonnet"},
    ]
    assert catalog.normalize("claude-code", rows) == [
        {"id": "claude-opus-5-5", "label": "Claude Opus 5.5"},
        {"id": "claude-fable-5-1[1m]", "label": "Claude Fable 5.1 (1M)"},
        {"id": "claude-haiku-4-5-20251001", "label": "Claude Haiku 4.5"},
    ]


def test_codex_hidden_invalid_and_duplicate_choices_are_not_advertised():
    assert catalog.normalize(
        "codex",
        [
            {"model": "gpt-6-astra", "displayName": "GPT-6-Astra"},
            {"model": "gpt-6-astra"},
            {"model": "private", "hidden": True},
            {"model": "bad\nmodel"},
            None,
        ],
    ) == [{"id": "gpt-6-astra", "label": "GPT-6-Astra"}]
    with pytest.raises(catalog.CatalogError):
        catalog.normalize("codex", [])


def fake_cli(monkeypatch, tmp_path: Path, script: str):
    path = tmp_path / "catalog_cli.py"
    path.write_text(script)
    real_spawn = asyncio.create_subprocess_exec
    processes = []
    commands = []

    async def spawn(*args, **kwargs):
        commands.append(args)
        proc = await real_spawn(sys.executable, str(path), **kwargs)
        processes.append(proc)
        return proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    return processes, commands


def test_codex_protocol_reads_all_pages_without_starting_a_thread(monkeypatch, tmp_path):
    script = """import sys,json,time
read=lambda:json.loads(sys.stdin.readline())
send=lambda d:print(json.dumps(d),flush=True)
assert read()['method']=='initialize'
send({'id':1,'result':{}})
assert read()['method']=='initialized'
assert read()['params']=={'includeHidden':False,'limit':100}
send({'id':2,'result':{'data':[{'model':'first'}],'nextCursor':'page2'}})
assert read()['params']['cursor']=='page2'
send({'id':2,'result':{'data':[{'model':'second'}]}})
time.sleep(60)
"""
    processes, commands = fake_cli(monkeypatch, tmp_path, script)
    assert asyncio.run(catalog.discover("codex")) == [
        {"id": "first", "label": "first"},
        {"id": "second", "label": "second"},
    ]
    assert commands == [("codex", "app-server")]
    assert all(p.returncode is not None for p in processes)


def test_claude_initializes_only_and_disables_tools_hooks_mcp_persistence(monkeypatch, tmp_path):
    script = """import sys,json,time
request=json.loads(sys.stdin.readline())
assert request=={'type':'control_request','request_id':'catalog','request':{'subtype':'initialize'}}
print(json.dumps({'type':'control_response','response':{'request_id':'catalog','response':{'models':[{'value':'opus','resolvedModel':'claude-opus-5-5'}]}}}),flush=True)
time.sleep(60)
"""
    processes, commands = fake_cli(monkeypatch, tmp_path, script)
    assert asyncio.run(catalog.discover("claude-code"))[0]["id"] == "claude-opus-5-5"
    cmd = commands[0]
    assert "--no-session-persistence" in cmd
    assert "--strict-mcp-config" in cmd
    assert json.loads(cmd[cmd.index("--settings") + 1])["disableAllHooks"] is True
    assert cmd[cmd.index("--tools") + 1] == ""
    assert all(p.returncode is not None for p in processes)


@pytest.mark.parametrize("mode", ["timeout", "cancel", "invalid", "exit"])
def test_probe_failures_reap_the_child(monkeypatch, tmp_path, mode):
    script = {"invalid": "print('not-json',flush=True)", "exit": "pass"}.get(
        mode, "import time;time.sleep(60)"
    )
    processes, _ = fake_cli(monkeypatch, tmp_path, script)
    real_timeout = asyncio.timeout
    if mode == "timeout":
        monkeypatch.setattr(asyncio, "timeout", lambda _: real_timeout(0.05))

    async def run():
        if mode == "cancel":
            task = asyncio.create_task(catalog.discover("codex"))
            while not processes:
                await asyncio.sleep(0.005)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            with pytest.raises(catalog.CatalogError):
                await catalog.discover("codex")
        assert processes and all(p.returncode is not None for p in processes)

    asyncio.run(run())


def test_concurrent_catalog_reads_share_success_and_failed_reads_can_retry(monkeypatch):
    calls = []

    async def discover(runtime):
        calls.append(runtime)
        await asyncio.sleep(0.01)
        if len(calls) == 1:
            raise catalog.CatalogError("Sign in first")
        return [{"id": "exact-version", "label": "Exact version"}]

    monkeypatch.setattr(catalog, "discover", discover)

    async def run():
        service = catalog.ModelCatalog()
        with pytest.raises(catalog.CatalogError):
            await service.choices("codex")
        results = await asyncio.gather(*(service.choices("codex") for _ in range(3)))
        assert len(calls) == 2
        assert results[0] == results[1] == results[2]

    asyncio.run(run())
