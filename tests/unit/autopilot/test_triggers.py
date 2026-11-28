"""Flows declare their triggers; putting one on autopilot saves which are on;
the GitHub issues trigger decides which issues start a run."""

import json
from pathlib import Path
from typing import Any

import pytest

from duckterm.autopilot import catalog, github_watch, store

FLOW = "autopilot/issue-to-merge"


def test_built_in_flow_lists_the_triggers_it_declares() -> None:
    flow = catalog.find(FLOW)
    assert flow.triggers == {
        "github-issue": "issue",
        "jira-issue": "issue",
        "slack-message": "issue",
    }


def _kit(tmp_path: Path, flow: dict[str, Any]) -> Path:
    kit = tmp_path / "kits" / "k"
    (kit / "flows").mkdir(parents=True)
    (kit / "skill-kit.toml").write_text(
        'manifest_version = 1\nname = "k"\ndescription = "d"\nversion = "1"\n'
        '[[components]]\nkind = "flow"\npath = "flows/f.json"\n'
    )
    (kit / "flows" / "f.json").write_text(json.dumps({"name": "f", "steps": [], **flow}))
    return tmp_path / "kits"


@pytest.mark.parametrize(
    ("flow", "error"),
    [
        ({"triggers": [{"type": "email"}]}, "unknown trigger"),
        ({"inputs": {}, "triggers": [{"type": "github-issue", "input": "issue"}]}, "undeclared"),
    ],
)
def test_bad_trigger_declarations_are_refused(
    tmp_path: Path, flow: dict[str, Any], error: str
) -> None:
    with pytest.raises(ValueError, match=error):
        catalog.flows(_kit(tmp_path, flow))


def test_a_flow_without_triggers_runs_only_by_hand(tmp_path: Path) -> None:
    assert catalog.flows(_kit(tmp_path, {})) == []


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("DUCKTERM_HOME", str(tmp_path))
    return tmp_path


def _body(**over: Any) -> dict[str, Any]:
    return {
        "flow": FLOW,
        "repo": "o/r",
        "triggers": {"github-issue": {"labels": [" bug", "autopilot", "bug"]}},
        **over,
    }


def test_saving_normalises_labels_and_names_the_autopilot(home: Path) -> None:
    entry = store.create(_body())
    assert entry["triggers"] == {"github-issue": {"labels": ["autopilot", "bug"]}}
    assert entry["name"] == "r issue-to-merge"
    assert store.load() == [entry]


def test_issues_come_from_the_code_repository_unless_another_is_given(home: Path) -> None:
    assert store.create(_body())["issue_repo"] == "o/r"
    entry = store.create(_body(issue_repo="o/reports"))
    assert (entry["repo"], entry["issue_repo"]) == ("o/r", "o/reports")
    # a record saved before issue_repo existed watches its code repository
    assert store.issue_repo({"repo": "o/r"}) == "o/r"
    with pytest.raises(ValueError, match="owner/name"):
        store.create(_body(issue_repo="not a repo"))


@pytest.mark.parametrize(
    ("over", "error"),
    [
        ({"flow": "autopilot/nope"}, "Choose a flow"),
        ({"repo": "not a repo"}, "owner/name"),
        ({"triggers": {}}, "at least one trigger"),
        ({"triggers": {"github-pr": {}}}, "cannot be started by github-pr"),
        ({"triggers": {"jira-issue": {}}}, "not available yet"),
    ],
)
def test_saving_refuses_what_the_flow_cannot_do(
    home: Path, over: dict[str, Any], error: str
) -> None:
    with pytest.raises(ValueError, match=error):
        store.create(_body(**over))
    assert store.load() == []


def test_pause_keeps_the_settings_and_delete_removes(home: Path) -> None:
    entry = store.create(_body(budget_m=9))
    paused = store.update(entry["id"], {"on": False})
    assert (paused["on"], paused["budget_m"]) == (False, 9)
    store.delete(entry["id"])
    with pytest.raises(KeyError):
        store.get(entry["id"])


class FakeGitHub:
    """Answers the four GitHub calls the watcher makes."""

    def __init__(self) -> None:
        self.issues: dict[int, dict[str, Any]] = {}
        self.labeled_by: dict[int, str] = {}
        self.writers = {"maintainer"}
        self.repos: dict[str, Any] = {"/repos/o/r": {"permissions": {"push": True}}}

    def add(self, number: int, labels: list[str], by: str = "maintainer", pr: bool = False) -> None:
        item: dict[str, Any] = {
            "number": number,
            "title": f"t{number}",
            "labels": [{"name": n} for n in labels],
            "user": {"login": "reporter"},
        }
        if pr:
            item["pull_request"] = {}
        self.issues[number] = item
        self.labeled_by[number] = by

    def __call__(self, path: str) -> Any:
        if path.startswith("/repos/o/r/issues?"):
            wanted = path.split("labels=")[1].split("&")[0] if "labels=" in path else None
            return [
                i
                for i in self.issues.values()
                if wanted is None or wanted in [lb["name"] for lb in i["labels"]]
            ]
        if path.endswith("/events?per_page=100"):
            n = int(path.split("/")[5])
            return [
                {"event": "labeled", "label": lb, "actor": {"login": self.labeled_by[n]}}
                for lb in self.issues[n]["labels"]
            ]
        if "/collaborators/" in path:
            login = path.split("/")[5]
            return {"permission": "write" if login in self.writers else "read"}
        if path.count("/") == 3:
            return self.repos.get(path)
        raise AssertionError(path)


def _poll(gh: FakeGitHub, tmp_path: Path, labels: list[str], limit: int = 5) -> list[int]:
    return [
        i["number"] for i in github_watch.poll(gh, "o/r", labels, tmp_path / "seen.json", limit)
    ]


def test_turning_on_never_starts_runs_for_issues_already_there(tmp_path: Path) -> None:
    gh = FakeGitHub()
    gh.add(1, ["autopilot"])
    assert _poll(gh, tmp_path, ["autopilot"]) == []
    gh.add(2, ["autopilot"])
    assert _poll(gh, tmp_path, ["autopilot"]) == [2]
    assert _poll(gh, tmp_path, ["autopilot"]) == []


def test_only_a_label_from_someone_with_write_access_starts_a_run(tmp_path: Path) -> None:
    gh = FakeGitHub()
    _poll(gh, tmp_path, ["autopilot"])
    gh.add(3, ["autopilot"], by="stranger")
    assert _poll(gh, tmp_path, ["autopilot"]) == []
    gh.labeled_by[3] = "maintainer"  # a maintainer labels it later
    assert _poll(gh, tmp_path, ["autopilot"]) == [3]


def test_any_listed_label_counts_and_pull_requests_are_skipped(tmp_path: Path) -> None:
    gh = FakeGitHub()
    _poll(gh, tmp_path, ["autopilot", "bug"])
    gh.add(4, ["bug"])
    gh.add(5, ["autopilot"])
    gh.add(6, ["bug"], pr=True)
    assert _poll(gh, tmp_path, ["autopilot", "bug"]) == [4, 5]


def test_runs_beyond_the_limit_wait_for_the_next_poll(tmp_path: Path) -> None:
    gh = FakeGitHub()
    _poll(gh, tmp_path, ["autopilot"])
    for n in (7, 8, 9):
        gh.add(n, ["autopilot"])
    assert _poll(gh, tmp_path, ["autopilot"], limit=2) == [7, 8]
    assert _poll(gh, tmp_path, ["autopilot"], limit=2) == [9]


@pytest.mark.parametrize(("answer", "runs"), [(None, [4]), ("new", [4]), ("run", [3, 4])])
def test_a_label_change_keeps_what_started_and_asks_about_what_now_matches(
    home: Path, monkeypatch: pytest.MonkeyPatch, answer: str | None, runs: list[int]
) -> None:
    from duckterm.autopilot import api

    gh = FakeGitHub()
    monkeypatch.setattr(api, "_github", lambda: gh)
    entry = store.create(_body(triggers={"github-issue": {"labels": ["autopilot"]}}))
    seen = api._seen(entry["id"])
    gh.add(1, ["autopilot"])
    github_watch.poll(gh, "o/r", ["autopilot"], seen, 5)
    gh.add(2, ["autopilot"])
    assert [i["number"] for i in github_watch.poll(gh, "o/r", ["autopilot"], seen, 5)] == [2]
    gh.add(3, ["bug"])
    edit: dict[str, Any] = {"triggers": {"github-issue": {"labels": ["autopilot", "bug"]}}}
    assert api.backlog(entry["id"], edit) == {"issues": [3]}
    api.update(entry["id"], {**edit, "backlog": answer} if answer else edit)
    gh.add(4, ["bug"])
    polled = github_watch.poll(gh, "o/r", ["autopilot", "bug"], seen, 5)
    assert [i["number"] for i in polled] == runs


def test_watching_another_issue_repository_starts_over_from_what_is_there(
    tmp_path: Path,
) -> None:
    # issue numbers from the old repository mean nothing in the new one
    gh = FakeGitHub()
    seen = tmp_path / "seen.json"
    github_watch.poll(gh, "o/r", ["autopilot"], seen, 5)
    gh.add(11, ["autopilot"])
    other = lambda path: gh(path.replace("/repos/o/reports/", "/repos/o/r/"))  # noqa: E731
    assert github_watch.poll(other, "o/reports", ["autopilot"], seen, 5) == []
    gh.add(12, ["autopilot"])
    assert [i["number"] for i in github_watch.poll(other, "o/reports", ["autopilot"], seen, 5)] == [
        12
    ]


@pytest.mark.parametrize(
    ("repos", "error"),
    [
        ({"/repos/o/r": {"permissions": {"push": False}}}, "can't push to o/r"),
        ({"/repos/o/r": {"permissions": {"push": True}}}, "can't reach o/reports"),
        ({}, "can't reach o/r"),
    ],
)
def test_a_setup_the_token_cannot_carry_out_is_refused(
    home: Path, monkeypatch: pytest.MonkeyPatch, repos: dict[str, Any], error: str
) -> None:
    from duckterm.autopilot import api

    gh = FakeGitHub()
    monkeypatch.setattr(api, "_github", lambda: gh)
    gh.repos = repos
    with pytest.raises(ValueError, match=error):
        api.create(_body(issue_repo="o/reports"))
    assert store.load() == []
    gh.repos = {
        "/repos/o/r": {"permissions": {"push": True}},
        "/repos/o/reports": {"full_name": "o/reports"},
    }
    entry = api.create(_body(issue_repo="o/reports"))
    gh.repos = repos
    with pytest.raises(ValueError, match=error):
        api.update(entry["id"], {"repo": "o/r"})
    # pausing touches no repository, so it works without GitHub
    assert api.update(entry["id"], {"on": False})["on"] is False


def test_autopilot_routes_belong_to_the_autopilot_flag() -> None:
    from duckterm import editions

    for url in (
        "/api/autopilot",
        "/api/autopilot?workspace=Team",
        "/api/autopilot/autopilots/ap-1/run",
        "/api/autopilot/runs/x",
    ):
        assert editions.request_features("GET", url) == ("autopilot",), url
    assert "autopilot" in editions.DEVELOPMENT


def test_a_workspace_sees_its_own_and_its_subfolders_flows_and_runs(home: Path) -> None:
    from duckterm.autopilot import api, runs

    mine = store.create(_body(workspace="Team/"))
    store.create(_body(workspace="Team/web"))
    store.create(_body(workspace="Teamwork"))
    store.create(_body())
    meta = {
        "id": "r1",
        "autopilot": mine["id"],
        "workspace": "Team",
        "state": "succeeded",
        "started_at": 1,
    }
    runs._write_meta(runs.runs_root() / "r1", meta)
    other = {**meta, "id": "r2", "workspace": "Teamwork"}
    runs._write_meta(runs.runs_root() / "r2", other)
    entries = [e for e in store.load() if api.in_workspace(e, "Team")]
    assert sorted(e["workspace"] for e in entries) == ["Team", "Team/web"]
    assert [r["id"] for r in runs.list_runs() if api.in_workspace(r, "Team")] == ["r1"]
