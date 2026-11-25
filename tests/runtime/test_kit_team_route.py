"""Create team over real HTTP: the record under the home, the workspace in the
store (whose connection belongs to the loop thread), and the kit enabled for
the new workspace; a workspace that fails to register leaves no record behind
so the next attempt succeeds."""

import asyncio
import json
from pathlib import Path

import pytest
from tests.runtime.test_agents_md import _request
from tests.runtime.test_harness_suites import KITS_ON

from duckterm import release_features
from duckterm.helpers import instance
from duckterm.kits import api
from duckterm.persistence.history import HistoryStore
from duckterm.server import Server


# Skill kits are also a release flag (#427); turn it on as test_harness_suites does.
@pytest.fixture(autouse=True)
def enabled_feature(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(release_features, "overrides", lambda: ({"skill_kits": True}, None))


SAMPLE = Path(__file__).resolve().parents[2] / "examples" / "skill-kits" / "sample-kit"


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
