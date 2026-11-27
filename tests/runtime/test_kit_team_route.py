"""Create team over real HTTP: the record under the home, the workspace in the
store (whose connection belongs to the loop thread), and the kit enabled for
the new workspace; a workspace that fails to register leaves no record behind
so the next attempt succeeds."""

import asyncio
import json
import subprocess
from pathlib import Path
from typing import Any

import pytest
from tests.runtime.test_agents_md import _request
from tests.runtime.test_harness_suites import KITS_ON

from duckterm import harness_preferences, release_features
from duckterm.core.orchestrator import Orchestrator
from duckterm.helpers import instance
from duckterm.kits import api
from duckterm.persistence.history import HistoryStore
from duckterm.server import Server


# Skill kits are also a release flag (#427); turn it on as test_harness_suites does.
@pytest.fixture(autouse=True)
def enabled_feature(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(release_features, "overrides", lambda: ({"skill_kits": True}, None))


SAMPLE = Path(__file__).resolve().parents[2] / "examples" / "metaharnesses" / "sample-kit"


async def _owner_post(port: int, token: str, path: str, payload: dict) -> tuple[int, dict]:
    body = json.dumps(payload).encode()
    head = (
        f"POST {path} HTTP/1.1\r\nHost: localhost\r\nX-Duckterm-Token: {token}\r\n"
        f"Content-Type: application/json\r\nContent-Length: {len(body)}\r\n\r\n"
    )
    return await _request(port, head.encode() + body, timeout=15)


def test_create_team_registers_the_workspace_and_recovers_from_a_failed_registration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(instance, "home", lambda: tmp_path / "dthome")
    store = HistoryStore(tmp_path / "db.sqlite")
    body = {"kit": "sample-kit", "team": "feature-squad", "kit_path": str(SAMPLE)}
    real_create_folder = HistoryStore.create_folder
    failures = {"left": 1}

    def flaky(self: HistoryStore, folder: str, *, now: int = 0) -> None:
        if failures["left"]:
            failures["left"] -= 1
            raise RuntimeError("disk full")
        real_create_folder(self, folder, now=now)

    async def scenario() -> list[tuple[int, dict]]:
        server = Server(history=store, edition=KITS_ON)
        srv = await asyncio.start_server(server.handle, "127.0.0.1", 0)
        port = srv.sockets[0].getsockname()[1]
        out = []
        async with srv:
            monkeypatch.setattr(HistoryStore, "create_folder", flaky)
            out.append(await _owner_post(port, server.token, "/kits/team", body))
            out.append(await _owner_post(port, server.token, "/kits/team", body))
            out.append(await _owner_post(port, server.token, "/kits/team", body))
        return out

    first, second, third = asyncio.run(scenario())
    assert first[0] == 500 and "could not be registered" in first[1]["error"]
    # the failed attempt left no record, so the retry is not a 409
    assert second[0] == 200, second
    assert second[1]["team"]["folder"] == "feature-squad"
    assert set(second[1]["team"]["roles"]) == {"dev", "reviewer"}
    assert "feature-squad" in store.folders()
    assert api.team_for_folder("feature-squad")["kit"] == "sample-kit"
    assert api.policies()["sample-kit"]["workspaces"] == ["feature-squad"]
    assert api.policies()["sample-kit"]["digest"] == api.kit_digest(SAMPLE)
    # a third attempt on an existing team is the usual refusal
    assert third[0] == 409


async def _owner_get(port: int, token: str, path: str) -> tuple[int, dict]:
    head = f"GET {path} HTTP/1.1\r\nHost: localhost\r\nX-Duckterm-Token: {token}\r\n\r\n"
    return await _request(port, head.encode(), timeout=15)


def test_create_team_installs_the_kit_and_starts_one_session_per_role_on_the_default_harness(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Owner, 2026-11-27: a team starts with its sessions. Each role gets one,
    in the team's folder, named for the role, opened with the role's card, on
    the harness the cascade picks; the kit is in the folder before the first
    one starts. `sessions: false` keeps the old create-only behaviour."""
    monkeypatch.setattr(instance, "home", lambda: tmp_path / "dthome")
    monkeypatch.setattr(harness_preferences, "present", lambda: ["claude-code", "codex"])
    project = tmp_path / "payments"
    project.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=project, check=True)
    store = HistoryStore(tmp_path / "db.sqlite")
    store.add_harness("sample-kit", str(SAMPLE), 1)
    launched: list[dict[str, Any]] = []

    async def fake_launch(self: Orchestrator, **kw: Any) -> str:
        launched.append(
            {
                **kw,
                "runtime": kw["runtime"].name,
                "kit_present": (project / ".claude" / "skills" / "review-diff").is_dir(),
            }
        )
        return f"key-{len(launched)}"

    monkeypatch.setattr(Orchestrator, "launch", fake_launch)
    body = {
        "kit": "sample-kit",
        "team": "feature-squad",
        "kit_path": str(SAMPLE),
        "project": str(project),
    }

    async def scenario() -> list[tuple[int, dict]]:
        server = Server(history=store, edition=KITS_ON)
        srv = await asyncio.start_server(server.handle, "127.0.0.1", 0)
        port = srv.sockets[0].getsockname()[1]
        out = []
        async with srv:
            out.append(await _owner_get(port, server.token, "/harness-preferences"))
            out.append(await _owner_post(port, server.token, "/kits/team", body))
            out.append(
                await _owner_post(
                    port, server.token, "/harness-preferences", {"default_harness": "codex"}
                )
            )
            out.append(
                await _owner_post(
                    port,
                    server.token,
                    "/kits/team",
                    {**body, "name": "Billing", "test": True},
                )
            )
            out.append(
                await _owner_post(
                    port, server.token, "/kits/team", {**body, "name": "Quiet", "sessions": False}
                )
            )
        return out

    prefs, created, saved, second, quiet = asyncio.run(scenario())
    assert prefs == (
        200,
        {"default_harness": None, "present": ["claude-code", "codex"], "effective": "claude-code"},
    )
    assert created[0] == 200, created
    assert created[1]["harness"] == "claude-code" and created[1]["failed"] == []
    assert [s["role"] for s in created[1]["sessions"]] == ["dev", "reviewer"]
    assert created[1]["team"]["sessions"] == created[1]["sessions"]
    dev, reviewer = launched[:2]
    assert dev["runtime"] == "claude-code" and dev["cwd"] == str(project) and dev["name"] == "dev"
    assert dev["kit_present"] is True  # installed before the first session started
    assert "Builds what was asked; raises the PR." in dev["prompt"]
    assert "Your work is reviewed by the reviewer." in dev["prompt"]
    assert reviewer["name"] == "reviewer" and "You may not push." in reviewer["prompt"]
    assert dev["test"] is False
    assert api.team_for_folder("feature-squad")["harness"] == "claude-code"
    assert api.launch_folder("key-1") == str(project)
    # the setting wins once set; `test` reaches the sessions
    assert saved[1]["effective"] == "codex"
    assert second[0] == 200 and second[1]["harness"] == "codex"
    assert launched[2]["runtime"] == "codex" and launched[2]["test"] is True
    assert quiet[0] == 200 and quiet[1].get("sessions") is None and len(launched) == 4
