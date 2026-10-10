"""Checkpoints cross the real bounded subprocess runner, without provider accounts."""

import asyncio
import json
import sys
from pathlib import Path

from test_memory import memory_rig, transcript

from duckterm import memory_provider
from duckterm.llm import summarizer

__all__ = ["memory_rig"]

SCRIPT = """
import json,os,sys,time
from pathlib import Path
log=Path(sys.argv[1])
prompt=sys.stdin.read()
with log.open('a') as stream:
    stream.write(json.dumps({'cwd':os.getcwd(),'internal':os.environ.get('DUCKTERM_INTERNAL'),
        'has_token':'DUCKTERM_SESSION_TOKEN' in os.environ})+'\\n')
if sys.argv[2]=='timeout':
    time.sleep(30)
if prompt.startswith('You are validating'):
    result={'accept':[],'done_next_action_ids':[],
        'summary_validation':{'ready':True,'reason_codes':[]}}
else:
    data=json.loads(prompt.split('MEMORY UPDATE:\\n',1)[1])
    row=data['records'][0]
    ref=row['source']+':'+row['version']+':'+row['record']
    result={'summary':'Keep the owner constraint.',
        'context':{'overview':'Keep the owner constraint.',
            'goals':[], 'constraints':[{'text':'Keep the owner constraint.','refs':[ref]}],
            'decisions':[], 'unfinished':[], 'questions':[], 'risks':[]}}
print(json.dumps(result))
"""


def setup_runner(monkeypatch, tmp, mode="success"):
    monkeypatch.delenv("DUCKTERM_SUMMARIZER", raising=False)
    monkeypatch.setenv("DUCKTERM_SESSION_TOKEN", "fixture-not-a-credential")
    monkeypatch.setattr(summarizer, "_auto_command", lambda: "claude -p")
    log = tmp / "runner.jsonl"
    monkeypatch.setattr(
        memory_provider,
        "arguments",
        lambda *args, **kwargs: [sys.executable, "-c", SCRIPT, str(log), mode],
    )
    return log


def test_checkpoint_generation_and_validation_use_isolated_subprocess(memory_rig, monkeypatch):
    server, _, _, tmp = memory_rig
    transcript(tmp, "agent-claude", "Keep the owner constraint.")
    log = setup_runner(monkeypatch, tmp)

    async def run():
        first = await server._create_checkpoint("agent", server.history.session("agent"), "manual")
        revision_id = first["record"]["summary_ref"]
        revision = server.digests.revision("agent", revision_id)
        assert first["record"]["summary_update"]["state"] == "updated"
        assert revision["summary"] == "Keep the owner constraint."
        assert revision["continuity"]["context"]["constraints"][0]["refs"]
        again = await server._create_checkpoint("agent", server.history.session("agent"), "manual")
        assert again["record"]["summary_ref"] == revision_id
        assert again["record"]["summary_update"]["state"] == "reused"

    asyncio.run(run())
    rows = [json.loads(line) for line in log.read_text().splitlines()]
    assert len(rows) == 2  # generation and review; the unchanged retry uses neither
    assert all(row["internal"] == "1" and not row["has_token"] for row in rows)
    assert all(not Path(row["cwd"]).exists() for row in rows)


def test_checkpoint_timeout_keeps_previous_revision_and_reaps_child(memory_rig, monkeypatch):
    server, _, _, tmp = memory_rig
    path = transcript(tmp, "agent-claude", "Keep the owner constraint.")
    setup_runner(monkeypatch, tmp)
    processes = []
    create = memory_provider.asyncio.create_subprocess_exec

    async def capture(*args, **kwargs):
        proc = await create(*args, **kwargs)
        processes.append(proc)
        return proc

    monkeypatch.setattr(memory_provider.asyncio, "create_subprocess_exec", capture)

    async def run():
        first = await server._create_checkpoint("agent", server.history.session("agent"), "manual")
        revision_id = first["record"]["summary_ref"]
        previous = server.history.session("agent")["progress"]
        with path.open("a") as stream:
            stream.write(
                json.dumps(
                    {"type": "user", "message": {"role": "user", "content": "New constraint"}}
                )
                + "\n"
            )
        setup_runner(monkeypatch, tmp, "timeout")
        monkeypatch.setattr(memory_provider, "CALL_TIMEOUT", 0.2)
        failed = await server._create_checkpoint("agent", server.history.session("agent"), "manual")
        assert failed["record"]["summary_update"]["reason"] == "provider_timeout"
        assert failed["record"]["summary_update"]["state"] == "failed"
        assert server.history.session("agent")["progress"] == previous
        assert (
            server.digests.revision("agent", revision_id)["summary"] == "Keep the owner constraint."
        )

    asyncio.run(run())
    assert len(processes) == 3
    assert all(proc.returncode is not None for proc in processes)
    assert processes[-1].returncode != 0
