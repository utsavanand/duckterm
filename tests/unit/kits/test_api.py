"""The Skill kits page's API over the engine: what the page sees before, during
and after an install, and how a flow run is started and watched."""

import json
import os
import time

import pytest
from test_skill_kits import SAMPLE

from duckterm.kits import api, runner


def _project(tmp_path):
    project = tmp_path / "proj"
    project.mkdir()
    return project


def test_plan_returns_the_inventory_and_the_dry_run(tmp_path):
    project = _project(tmp_path)
    out = api.plan_for({"kit": str(SAMPLE), "project": str(project), "harnesses": ["claude-code"]})
    inv = out["inventory"]
    assert inv["name"] == "sample-kit" and inv["valid"] is True
    assert {k: len(v) for k, v in inv["components"].items()}["skill"] == 2
    assert inv["procedures"][0]["name"] == "feature-squad"
    assert set(inv["procedures"][0]["roles"]) == {"dev", "reviewer"}
    assert inv["options"]["persona"]["choices"] == ["professional", "sport"]
    rows = {r["component"]: r for r in out["plan"]["rows"]}
    assert rows["sample-kit/skill/review-diff"]["harnesses"] == {"claude-code": "installed"}
    assert out["plan"]["conflicts"] == []
    assert not (project / ".claude").exists()


def test_bad_inputs_are_refused_with_a_status(tmp_path):
    project = _project(tmp_path)
    with pytest.raises(api.KitApiError) as e:
        api.plan_for({"kit": str(tmp_path / "nope"), "project": str(project)})
    assert e.value.status == 400
    with pytest.raises(api.KitApiError, match="unknown harness"):
        api.plan_for({"kit": str(SAMPLE), "project": str(project), "harnesses": ["vim"]})
    with pytest.raises(api.KitApiError, match="project is required"):
        api.overview("")


def test_install_overview_verify_and_uninstall_round_trip(tmp_path):
    project = _project(tmp_path)
    (project / ".claude").mkdir()
    (project / ".claude" / "settings.json").write_bytes(
        b'{"permissions": {"allow": ["Bash(ls *)"]}}'
    )
    before = (project / ".claude" / "settings.json").read_bytes()
    out = api.install_kit(
        {"kit": str(SAMPLE), "project": str(project), "harnesses": ["claude-code"]}
    )
    assert out["report"]["kit"] == "sample-kit" and out["report"]["written"]
    view = api.overview(str(project))
    kit = view["kits"][0]
    assert kit["name"] == "sample-kit" and kit["harnesses"] == ["claude-code"]
    assert kit["verify"]["ok"] is True and kit["trusted"] is True
    assert kit["flows"] == ["review"]
    assert kit["skills"]["review-diff"]["description"].startswith("Reviews a diff")
    assert kit["skills"]["review-diff"]["effects"] == "read-only"
    d = kit["details"]
    assert {x["name"] for x in d["skill"]} == {"review-diff", "raise-pr"}
    assert d["agent"][0]["name"] == "reviewer" and d["agent"][0]["description"]
    assert any(x["load"] == "on demand" for x in d["knowledge"])
    assert {x["event"] for x in d["hook"]} == {"before_tool", "stop"}
    assert d["permission"][0]["rules"][1] == {
        "tool": "shell",
        "match": "rm -rf *",
        "decision": "deny",
    }
    assert d["flow"][0]["steps"] == [{"id": "verdict", "kind": "decide"}]
    assert view["procedures"][0]["name"] == "feature-squad"
    assert view["procedures"][0]["kit"] == "sample-kit"
    assert view["procedures"][0]["rules"][0] == {"role": "dev", "review_by": "reviewer"}
    assert "claude-code" in view["harnesses_present"]
    (project / ".claude" / "skills" / "review-diff" / "SKILL.md").write_text("edited\n")
    assert api.verify_kits({"project": str(project)})["reports"][0]["drifted"]
    api.uninstall_kit({"project": str(project), "name": "sample-kit"})
    assert api.overview(str(project))["kits"] == []
    assert (project / ".claude" / "settings.json").read_bytes() == before


def test_start_run_returns_the_run_id_before_the_step_finishes(tmp_path, monkeypatch):
    project = _project(tmp_path)
    api.install_kit({"kit": str(SAMPLE), "project": str(project), "harnesses": ["claude-code"]})
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake = bin_dir / "claude"
    fake.write_text(
        '#!/bin/sh\nsleep 1\nmkdir -p "$SKILL_KIT_OUTPUT_DIR"\n'
        'printf x > "$SKILL_KIT_OUTPUT_DIR/findings.md"\n'
        'printf \'{"structured_output":{"verdict":"approve","reason":"ok"}}\'\n'
    )
    fake.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ['PATH']}")
    monkeypatch.setattr(runner, "_claude_trusts", lambda project: True)
    t0 = time.time()
    out = api.start_run(
        {"project": str(project), "flow": "sample-kit/review", "harness": "claude-code"}
    )
    assert time.time() - t0 < 5
    run_id = out["run_id"]
    assert run_id.startswith("r-")
    detail = api.run_detail(str(project), run_id)["run"]
    assert detail["state"] in ("running", "succeeded")
    api._threads[run_id].join(timeout=20)
    final = api.run_detail(str(project), run_id)["run"]
    assert final["state"] == "succeeded", final.get("failure")
    assert final["attempts"][0]["choice"] == "approve"
    assert [r["run_id"] for r in api.runs(str(project))["runs"]] == [run_id]
    with pytest.raises(api.KitApiError) as e:
        api.start_run(
            {"project": str(project), "flow": "sample-kit/nope", "harness": "claude-code"}
        )
    assert e.value.status == 400
    with pytest.raises(api.KitApiError) as e:
        api.run_detail(str(project), "r-missing")
    assert e.value.status == 404
    with pytest.raises(api.KitApiError, match="already succeeded"):
        api.answer_gate({"project": str(project), "step": "x", "answer": "approve"}, run_id)
    assert (
        json.loads((project / ".duckterm" / "runs" / run_id / "run.json").read_text())["state"]
        == "succeeded"
    )


def test_create_team_records_the_procedure_under_the_home_and_refuses_twice(tmp_path, monkeypatch):
    from duckterm.helpers import instance

    monkeypatch.setattr(instance, "home", lambda: tmp_path / "home")
    project = _project(tmp_path)
    api.install_kit({"kit": str(SAMPLE), "project": str(project), "harnesses": ["claude-code"]})
    out = api.create_team({"project": str(project), "kit": "sample-kit", "team": "feature-squad"})
    team = out["team"]
    assert team["folder"] == "feature-squad" and team["kit_version"] == "0.1.0"
    assert set(team["roles"]) == {"dev", "reviewer"}
    assert team["rules"][0] == {"role": "dev", "review_by": "reviewer"}
    assert api.teams()["teams"][0]["folder"] == "feature-squad"
    assert api.team_for_folder("feature-squad")["team"] == "feature-squad"
    assert api.team_for_folder("other") is None
    with pytest.raises(api.KitApiError) as e:
        api.create_team({"project": str(project), "kit": "sample-kit", "team": "feature-squad"})
    assert e.value.status == 409
    named = api.create_team(
        {
            "project": str(project),
            "kit": "sample-kit",
            "team": "feature-squad",
            "name": "Payments squad",
        }
    )
    assert named["team"]["folder"] == "Payments squad"
    with pytest.raises(api.KitApiError) as e:
        api.create_team({"project": str(project), "kit": "sample-kit", "team": "nope"})
    assert e.value.status == 404
    with pytest.raises(api.KitApiError, match="without slashes"):
        api.create_team(
            {"project": str(project), "kit": "sample-kit", "team": "feature-squad", "name": "a/b"}
        )
