import asyncio
import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from duckterm.llm import backend
from duckterm.review import history, lists, workspace

from .test_cards import serve

NO_SIGNING = [
    "-c",
    "tag.gpgsign=false",
    "-c",
    "tag.forceSignAnnotated=false",
    "-c",
    "commit.gpgsign=false",
]


def run(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *NO_SIGNING, "-C", str(repo), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def write(repo: Path, name: str, text: str) -> None:
    (repo / name).parent.mkdir(parents=True, exist_ok=True)
    (repo / name).write_text(text)


def commit(repo: Path, message: str) -> str:
    run(repo, "add", "-A")
    run(repo, "commit", "-q", "-m", message)
    return run(repo, "rev-parse", "HEAD")


@pytest.fixture
def tagged(git_repo: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(lists.git, "github_slug", lambda repo: None)
    monkeypatch.setattr(lists.git, "remote_heads", lambda repo: None)
    lists._cache.clear()
    write(git_repo, "src/duckterm/server.py", "from duckterm.persistence import history\n")
    write(git_repo, "src/duckterm/persistence/history.py", "X = 1\n")
    write(
        git_repo, "web/src/App.tsx", 'import { api } from "./api";\nexport const App = () => api;\n'
    )
    write(git_repo, "web/src/api.ts", "export const api = 1;\n")
    commit(git_repo, "v1 tree")
    run(git_repo, "tag", "-m", "release", "v0.1.0")
    run(git_repo, "checkout", "-q", "-b", "feature")
    write(git_repo, "src/duckterm/review/lists.py", "from duckterm.review import git\n")
    write(git_repo, "src/duckterm/review/git.py", "Y = 2\n")
    commit(git_repo, "add review package")
    run(git_repo, "checkout", "-q", "main")
    run(git_repo, "merge", "-q", "--no-ff", "-m", "Merge pull request #7 from x/feature", "feature")
    write(git_repo, "docs/notes.md", "# notes\n")
    commit(git_repo, "docs")
    run(git_repo, "tag", "-m", "release", "v0.2.0")
    run(git_repo, "checkout", "-q", "-b", "duckterm/pr")
    write(git_repo, "web/src/api.ts", "export const api = 2;\n")
    commit(git_repo, "bump api")
    run(git_repo, "checkout", "-q", "main")
    return git_repo


def test_releases_list_tags_with_merges_and_touched_paths(tagged: Path) -> None:
    rows = history.releases(str(tagged))
    assert [r["tag"] for r in rows] == ["v0.1.0", "v0.2.0"]
    assert rows[0]["previous"] is None and rows[1]["previous"] == "v0.1.0"
    assert rows[0]["at"] > 0
    assert [(m["pr"], m["subject"]) for m in rows[1]["merges"]] == [
        (7, "Merge pull request #7 from x/feature")
    ]
    assert history.touched_releases(str(tagged), rows, ["web/src/api.ts"]) == ["v0.1.0"]
    assert history.touched_releases(str(tagged), rows, ["src/duckterm/review/git.py"]) == ["v0.2.0"]


def test_import_graph_groups_files_and_edges(tagged: Path) -> None:
    graph = history.import_graph(str(tagged), "v0.2.0")
    assert set(graph["groups"]) == {
        "src/duckterm/",
        "src/duckterm/persistence",
        "src/duckterm/review",
        "web/src/",
    }
    edges = {(a, b): n for a, b, n in graph["edges"]}
    assert edges[("src/duckterm/", "src/duckterm/persistence")] == 1
    assert ("web/src/", "web/src/") not in edges
    assert graph["groups"]["src/duckterm/review"] == [
        "src/duckterm/review/git.py",
        "src/duckterm/review/lists.py",
    ]


MODULES = [
    {
        "id": "server",
        "name": "Server",
        "lane": "Server",
        "files": ["src/duckterm/"],
        "what": "HTTP",
    },
    {
        "id": "db",
        "name": "Persistence",
        "lane": "Server",
        "files": ["src/duckterm/persistence"],
        "what": "SQLite",
    },
    {"id": "web", "name": "Dashboard", "lane": "Clients", "files": ["web/src/"], "what": "React"},
]
REVIEW = {
    "id": "review",
    "name": "Review",
    "lane": "Server",
    "files": ["src/duckterm/review", "nope"],
    "what": "PRs",
}


def test_build_map_caches_and_compare_marks_modules(
    tagged: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(backend, "ready", lambda: True)
    prompts: list[str] = []

    async def generate(harness: str, model: str, prompt: str, schema: Any = None, **_: Any) -> str:
        prompts.append(prompt)
        modules = list(MODULES) + ([REVIEW] if "v0.2.0" in prompt else [])
        edges = [["server", "db"], ["web", "server"], ["review", "ghost"]]
        return json.dumps({"modules": modules, "edges": edges})

    serve(monkeypatch, generate)
    repo = str(tagged)
    first = asyncio.run(history.build_map(repo, None, "v0.1.0"))
    assert first["status"] == "ready"
    assert [m["id"] for m in first["modules"]] == ["server", "db", "web"]
    assert first["edges"] == [["server", "db"], ["web", "server"]]
    assert history.map_status(repo, None, ["v0.1.0", "v0.2.0"]) == {
        "v0.1.0": "ready",
        "v0.2.0": "missing",
    }
    assert asyncio.run(history.build_map(repo, None, "v0.1.0")) == first
    assert len(prompts) == 1  # the second call came from the cache
    second = asyncio.run(history.build_map(repo, None, "v0.2.0"))
    review = next(m for m in second["modules"] if m["id"] == "review")
    assert review["files"] == ["src/duckterm/review"]  # unknown group dropped
    diff = history.compare(repo, first, second)
    assert diff["marks"] == {
        "server": "unchanged",
        "db": "unchanged",
        "web": "unchanged",
        "review": "added",
    }
    assert diff["edges_added"] == [] and diff["edges_removed"] == []
    assert diff["files_changed"] == 3


def test_build_map_without_provider_is_omitted_with_the_graph(
    tagged: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(backend, "ready", lambda: False)
    value = asyncio.run(history.build_map(str(tagged), None, "v0.1.0"))
    assert value["status"] == "omitted"
    assert "No Claude Code or Codex CLI" in value["reason"]
    assert "src/duckterm/" in value["graph"]["groups"]
    assert history.map_status(str(tagged), None, ["v0.1.0"]) == {"v0.1.0": "omitted"}


def test_compare_marks_changed_modules_by_changed_files(tagged: Path) -> None:
    older = {
        "tag": "v0.1.0",
        "modules": [{"id": "web", "files": ["web/src/"]}, {"id": "old", "files": ["mac"]}],
        "edges": [["web", "old"]],
    }
    newer = {
        "tag": "v0.2.0",
        "modules": [
            {"id": "web", "files": ["web/src/"]},
            {"id": "db", "files": ["src/duckterm/persistence"]},
        ],
        "edges": [["web", "db"]],
    }
    diff = history.compare(str(tagged), older, newer)
    assert diff["marks"] == {"web": "unchanged", "db": "added", "old": "removed"}
    assert diff["removed_modules"] == [{"id": "old", "files": ["mac"]}]
    assert diff["edges_added"] == [["web", "db"]] and diff["edges_removed"] == [["web", "old"]]
    resolved = workspace.resolve(str(tagged), "duckterm/pr")
    assert resolved.base == "main"
