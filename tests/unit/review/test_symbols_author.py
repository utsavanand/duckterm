import subprocess
from pathlib import Path

import pytest

from duckterm.persistence.history import HistoryStore
from duckterm.review import author, lists, symbols, workspace

NO_SIGNING = ["-c", "tag.gpgsign=false", "-c", "commit.gpgsign=false"]


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


PY_BASE = """
def helper(x):
    return x + 1


class Store:
    def save(self, value):
        return helper(value)

    def load(self):
        return 1
"""
PY_HEAD = """
def helper(x):
    return x + 2


class Store:
    def save(self, value):
        return helper(value)

    def load(self, default=None):
        return default or 1


def brand_new():
    return helper(0)
"""


TS_BASE = (
    "export function fetchRows(key: string) {\n  return key;\n}\nexport const unused = () => 1;\n"
)
TS_HEAD = (
    "export function fetchRows(key: string, refresh = false) {\n"
    "  return key + String(refresh);\n}\n"
)
APP = 'import { fetchRows } from "./api";\nexport function App() {\n  return fetchRows("x");\n}\n'
TS_SAMPLE = (
    "import { html, type X } from './r';\nexport function one() {\n  return html();\n}\n"
    "const two = async () => 1;\nexport class Three {}\n"
)


@pytest.fixture
def repo(git_repo: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(lists.git, "github_slug", lambda repo: None)
    monkeypatch.setattr(lists.git, "remote_heads", lambda repo: None)
    lists._cache.clear()
    write(git_repo, "src/duckterm/core.py", PY_BASE)
    write(
        git_repo,
        "src/duckterm/cli.py",
        "from duckterm.core import helper\n\ndef main():\n    return helper(1)\n",
    )
    write(
        git_repo,
        "tests/unit/test_core.py",
        "from duckterm.core import Store\n\ndef test_load():\n    assert Store().load() == 1\n",
    )
    write(git_repo, "web/src/api.ts", TS_BASE)
    write(git_repo, "web/src/App.tsx", APP)
    write(git_repo, "web/node_modules/x/index.js", "function fetchRows() {}\n")
    commit(git_repo, "base")
    run(git_repo, "checkout", "-q", "-b", "duckterm/feature")
    write(git_repo, "src/duckterm/core.py", PY_HEAD)
    write(git_repo, "web/src/api.ts", TS_HEAD)
    commit(git_repo, "change helper, load and fetchRows; add brand_new; drop unused")
    run(git_repo, "checkout", "-q", "main")
    return git_repo


def test_python_and_typescript_symbols_are_indexed() -> None:
    defs, refs = symbols.python_symbols("x.py", PY_HEAD)
    assert [(d["name"], d["kind"]) for d in defs] == [
        ("helper", "function"),
        ("Store", "class"),
        ("Store.save", "method"),
        ("Store.load", "method"),
        ("brand_new", "function"),
    ]
    assert {"helper", "value", "default"} <= refs
    ts_defs, ts_refs = symbols.ts_symbols("a.ts", TS_SAMPLE)
    assert [(d["name"], d["kind"], d["line"], d["end"]) for d in ts_defs] == [
        ("one", "function", 2, 4),
        ("two", "function", 5, 5),
        ("Three", "class", 6, 6),
    ]
    assert {"html", "type X"} & ts_refs == {"html"} or "html" in ts_refs
    assert symbols.indexable("web/node_modules/x/index.js") is False
    assert symbols.indexable("docs/a.md") is False


def test_blast_radius_finds_callers_inside_and_outside_the_diff(repo: Path) -> None:
    target = workspace.resolve(str(repo), "duckterm/feature")
    payload = workspace.diff_payload(target, since=None, whitespace=True)
    radius = symbols.blast_radius(
        str(repo), None, target.base_sha, target.head_sha, payload["files"]
    )
    rows = {r["symbol"]: r for r in radius["symbols"]}
    assert set(rows) == {"helper", "Store.load", "brand_new", "fetchRows", "unused"}
    assert rows["helper"]["change"] == "changed"
    assert rows["helper"]["callers_outside"] == ["src/duckterm/cli.py"]
    assert rows["helper"]["callers_in_diff"] == []
    assert rows["helper"]["tested"] is False
    assert rows["Store.load"]["callers_outside"] == ["tests/unit/test_core.py"]
    assert rows["Store.load"]["tested"] is True
    assert rows["brand_new"]["change"] == "added" and rows["brand_new"]["outside_count"] == 0
    assert rows["unused"]["change"] == "removed"
    assert rows["fetchRows"]["callers_outside"] == ["web/src/App.tsx"]
    assert radius["verdict"] == "5 changed symbols · 3 called outside the diff · 4 untested"
    assert radius["diagram"].startswith("graph LR")
    assert '["cli.py"] -->' in radius["diagram"]
    # The index is cached per revision.
    cached = symbols.cache_dir(str(repo), None) / f"{target.head_sha}.json"
    assert cached.exists()
    assert radius["index"]["head_files"] == 5


def test_trajectory_from_a_recorded_session(tmp_path: Path) -> None:
    history = HistoryStore(tmp_path / "db.sqlite")
    key = "author-probe"

    def event(ts: int, event_type: str, **payload: object) -> None:
        history.record(
            {
                "_id": f"e{ts}",
                "_ts": ts,
                "event_type": event_type,
                "session_key": key,
                "test": True,
                **payload,
            }
        )

    event(1, "SessionStart")
    event(2, "UserPromptSubmit", prompt="Build the PR list")
    event(3, "PreToolUse", tool_name="Bash", tool_input={"command": "git checkout -b duckterm/x"})
    event(4, "PreToolUse", tool_name="Bash", tool_input={"command": "pytest tests/unit/review -q"})
    event(5, "PreToolUse", tool_name="Bash", tool_input={"command": "git commit -m 'x / y / z'"})
    event(6, "UserPromptSubmit", prompt="Drop the widget approach")
    event(7, "PreToolUse", tool_name="Bash", tool_input={"command": "git reset --hard HEAD~1"})
    event(8, "PreToolUse", tool_name="Bash", tool_input={"command": "ls"})
    event(
        9, "PreToolUse", tool_name="Bash", tool_input={"command": "git push -u origin duckterm/x"}
    )
    history.set_meta(key, name="main-dev")
    history.add_checkpoint(
        checkpoint_id="c1",
        session_key=key,
        label="Checkpoint 1",
        summary="Lists done.",
        record={"format": "v1"},
        markdown_path=None,
        created_at=6,
    )
    facts = author.facts(history, key)
    assert facts["session"]["name"] == "main-dev"
    assert len(facts["events"]) == 9
    story = author.trajectory(facts)
    assert story["turns"] == 2 and story["commits"] == 1 and story["pushes"] == 1
    assert [(e["turn"], e["kind"]) for e in story["events"]] == [
        (1, "prompt"),
        (1, "verify"),
        (1, "commit"),
        (2, "prompt"),
        (None, "checkpoint"),
        (2, "abandon"),
        (2, "push"),
    ]
    assert story["abandoned_commands"][0]["text"] == "git reset --hard HEAD~1"
    assert story["verification"][0]["text"].startswith("pytest")
    prompt = author.prompt({"title": "PR"}, story, {"type": "object"})
    assert "[2] abandon: git reset --hard HEAD~1" in prompt
    assert "Checkpoint 1: Lists done." in prompt
