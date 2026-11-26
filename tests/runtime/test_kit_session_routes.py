"""The session kit routes answer to a session credential: the run goes in the
folder DuckTerm launched the session in, whatever the body names and wherever
its hooks have since moved the row; permission flags are ignored; the session
sees only runs it started; there is no gate to answer; a session with no
launch record gets nothing."""

import asyncio
import json
import os
from pathlib import Path

import pytest
from tests.runtime.test_agents_md import _request
from tests.runtime.test_harness_suites import KITS_ON

from duckterm import release_features
from duckterm.helpers import instance
from duckterm.kits import api, runner
from duckterm.persistence.history import HistoryStore
from duckterm.server import Server


# Skill kits are also a release flag (#427); turn it on as test_harness_suites does.
@pytest.fixture(autouse=True)
def enabled_feature(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(release_features, "overrides", lambda: ({"skill_kits": True}, None))


SAMPLE = Path(__file__).resolve().parents[2] / "examples" / "metaharnesses" / "sample-kit"


async def _session_post(port: int, token: str, route: str, payload: dict) -> tuple[int, dict]:
    body = json.dumps(payload).encode()
    head = (
        f"POST /api/v1/session/kits/{route} HTTP/1.1\r\nHost: localhost\r\n"
        f"Authorization: Bearer {token}\r\nContent-Type: application/json\r\n"
        f"Content-Length: {len(body)}\r\n\r\n"
    )
    return await _request(port, head.encode() + body, timeout=15)


def test_a_session_runs_in_its_own_folder_sees_its_own_runs_and_answers_no_gate(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(instance, "home", lambda: tmp_path / "dthome")
    mine, theirs = tmp_path / "mine", tmp_path / "theirs"
    for folder in (mine, theirs):
        folder.mkdir()
        api.install_kit({"kit": str(SAMPLE), "project": str(folder), "harnesses": ["claude-code"]})
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake = bin_dir / "claude"
    fake.write_text(
        '#!/bin/sh\nmkdir -p "$SKILL_KIT_OUTPUT_DIR"\necho "$@" > "$SKILL_KIT_OUTPUT_DIR/argv"\n'
        'printf x > "$SKILL_KIT_OUTPUT_DIR/findings.md"\n'
        'printf \'{"structured_output":{"verdict":"approve","reason":"ok"}}\'\n'
    )
    fake.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ['PATH']}")
    monkeypatch.setattr(runner, "_claude_trusts", lambda project: True)
    store = HistoryStore(tmp_path / "db.sqlite")
    for key, cwd in (("a", mine), ("b", theirs), ("c", theirs)):
        store.record(
            {
                "_id": key,
                "_ts": 1,
                "event_type": "SessionStart",
                "session_key": key,
                "cwd": str(cwd),
                "test": True,
            }
        )
        store.set_meta(key, name=f"Session {key}", group="work")
    api.remember_launch("a", str(mine))
    api.remember_launch("b", str(theirs))
    # a's hooks since reported another folder (a `claude` the agent started in
    # theirs): the row moved, the launch record did not
    store.record(
        {
            "_id": "a-moved",
            "_ts": 2,
            "event_type": "PostToolUse",
            "session_key": "a",
            "cwd": str(theirs),
            "repo_path": str(theirs),
        }
    )
    assert store.session("a")["cwd"] == str(theirs)
    tokens = {
        k: store.session_api.enroll(k, {"root": "work", "purpose": "p"})["token"] for k in "abc"
    }

    async def scenario() -> dict:
        server = Server(history=store, edition=KITS_ON)
        srv = await asyncio.start_server(server.handle, "127.0.0.1", 0)
        port = srv.sockets[0].getsockname()[1]
        out: dict = {}
        async with srv:
            # the body names another folder and asks for yolo: both are ignored
            status, started = await _session_post(
                port,
                tokens["a"],
                "run",
                {
                    "project": str(theirs),
                    "flow": "sample-kit/review",
                    "harness": "claude-code",
                    "yolo": True,
                },
            )
            assert status == 200, started
            run_id = out["run_id"] = started["run_id"]
            for _ in range(100):
                if not api._threads[run_id].is_alive():
                    break
                await asyncio.sleep(0.2)
            out["gate"] = await _session_post(
                port,
                tokens["a"],
                "gate",
                {"run_id": run_id, "step": "verdict", "answer": "approve"},
            )
            out["a_runs"] = await _session_post(port, tokens["a"], "runs", {})
            out["b_runs"] = await _session_post(port, tokens["b"], "runs", {"project": str(mine)})
            out["b_detail"] = await _session_post(
                port, tokens["b"], "run-detail", {"project": str(mine), "run_id": run_id}
            )
            out["a_detail"] = await _session_post(
                port, tokens["a"], "run-detail", {"run_id": run_id}
            )
            out["c_run"] = await _session_post(
                port, tokens["c"], "run", {"flow": "sample-kit/review", "harness": "claude-code"}
            )
        return out

    out = asyncio.run(scenario())
    run_id = out["run_id"]
    run_dir = mine / ".duckterm" / "runs" / run_id
    assert (run_dir / "run.json").is_file()
    assert not (theirs / ".duckterm" / "runs").exists()
    argv = (run_dir / "verdict" / "1" / "argv").read_text()
    assert "--dangerously-skip-permissions" not in argv and "--permission-mode" in argv
    assert out["gate"][0] == 404
    assert [r["run_id"] for r in out["a_runs"][1]["runs"]] == [run_id]
    assert out["b_runs"] == (200, {"project": str(theirs.resolve()), "runs": []})
    assert out["b_detail"][0] == 404
    assert out["a_detail"][0] == 200
    assert out["a_detail"][1]["run"]["state"] == "succeeded", out["a_detail"][1]["run"]
    assert out["c_run"][0] == 409 and "no launch folder" in out["c_run"][1]["error"]
