import asyncio
import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from duckterm.core.session_api import APIError
from duckterm.llm import backend
from duckterm.review import cards, chat, lists, store, workspace

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


def serve(monkeypatch: pytest.MonkeyPatch, generate: Any) -> None:
    """Answer backend calls with a fake harness reply."""

    async def complete(prompt: str, *, tier: str, schema: Any = None) -> backend.Reply:
        return backend.Reply(await generate("claude-code", "sonnet", prompt, schema), "harness", "")

    monkeypatch.setattr(backend, "complete", complete)


@pytest.fixture
def target(git_repo: Path, monkeypatch: pytest.MonkeyPatch) -> workspace.Target:
    monkeypatch.setattr(lists.git, "github_slug", lambda repo: None)
    monkeypatch.setattr(lists.git, "remote_heads", lambda repo: None)
    monkeypatch.setattr(backend, "ready", lambda: True)
    lists._cache.clear()
    write(git_repo, "src/duckterm/server.py", "def route():\n    return 1\n")
    write(git_repo, "src/duckterm/persistence/db.py", "TABLES = []\n")
    write(git_repo, "docs/notes.md", "# Notes\n")
    commit(git_repo, "base")
    run(git_repo, "checkout", "-q", "-b", "duckterm/feature")
    write(git_repo, "src/duckterm/server.py", "def route():\n    return 2\n")
    write(git_repo, "src/duckterm/persistence/db.py", "TABLES = ['a']\n")
    write(git_repo, "docs/notes.md", "# Notes\n\nMore.\n")
    commit(git_repo, "Add table a and bump route\n\nAlso touches docs.")
    run(git_repo, "checkout", "-q", "main")
    return workspace.resolve(str(git_repo), "duckterm/feature")


def payload_for(target: workspace.Target) -> dict[str, Any]:
    return workspace.diff_payload(target, since=None, whitespace=True)


REPLIES: dict[str, dict[str, Any]] = {
    "tests": {"coverage": [], "gaps": [], "suggested": []},
    "guardrails": {"findings": []},
    "blast": {
        "consequence": "Callers of route see 2.",
        "risks": [{"symbol": "route", "note": "x"}, {"symbol": "ghost", "note": "y"}],
    },
    "author": {"trajectory": "t", "abandoned": [], "decisions": [], "verification": "v"},
    "intent": {
        "summary": "Adds table a.\n\nBumps the route.",
        "claims": [
            {
                "claim": "Adds table a",
                "in_diff": "true",
                "evidence": ["src/duckterm/persistence/db.py"],
            },
            {
                "claim": "Touches docs",
                "in_diff": "true",
                "evidence": ["docs/notes.md", "docs/missing.md"],
            },
            {"claim": "Adds tests", "in_diff": "false", "evidence": []},
        ],
        "unclaimed": [
            {"path": "src/duckterm/server.py", "what": "route returns 2"},
            {"path": "nope.py", "what": "x"},
        ],
        "diagram": "graph LR\n  db --> server",
    },
    "walkthrough": {
        "stops": [
            {
                "path": "src/duckterm/persistence/db.py",
                "hunk": 1,
                "title": "Table",
                "why": "Data first",
            },
            {"path": "src/duckterm/server.py", "hunk": 9, "title": "Route", "why": "Caller"},
            {"path": "ghost.py", "hunk": 1, "title": "Ghost", "why": "Not in diff"},
        ]
    },
    "risk": {
        "reasons": [
            {"path": "src/duckterm/persistence/db.py", "reason": "Schema drift"},
            {"path": "docs/notes.md", "reason": "ignored"},
        ]
    },
    "behaviour": {
        "rows": [
            {
                "situation": "route called",
                "before": "1",
                "after": "2",
                "who_notices": "owner",
                "regression_risk": "high",
                "evidence": ["src/duckterm/server.py"],
            }
        ]
    },
    "questions": {
        "items": [
            {
                "kind": "unverified",
                "text": "Where are the tests?",
                "paths": ["src/duckterm/server.py", "x.py"],
            },
            {"kind": "manual", "text": "Run it", "paths": []},
        ]
    },
}


def fake_generate(  # type: ignore[no-untyped-def]
    calls: list[str], *, fail: set[str] = frozenset(), garbage_first: set[str] = frozenset()
):
    seen: dict[str, int] = {}

    async def generate(
        harness: str, model: str, prompt: str, schema: Any = None, *, user_settings: bool = False
    ) -> str:
        card = "unknown"
        for name, marker in (
            ("questions", "# Task: Open questions"),
            ("intent", "# Task: Intent vs diff"),
            ("walkthrough", "# Task: Walkthrough"),
            ("behaviour", "# Task: Behaviour changes"),
            ("risk", "# Task: Risk map"),
            ("tests", "# Task: Tests"),
            ("guardrails", "# Task: Guardrails"),
            ("blast", "# Task: Blast radius"),
            ("author", "# Task: How the author got here"),
        ):
            if marker in prompt:
                card = name
                break
        calls.append(card)
        if card in fail:
            raise APIError(503, "provider down")
        seen[card] = seen.get(card, 0) + 1
        if card in garbage_first and seen[card] == 1:
            return "not json at all"
        return "```json\n" + json.dumps(REPLIES[card]) + "\n```"

    return generate


def test_cards_are_checked_against_the_diff_and_cached_per_head(
    target: workspace.Target, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []
    serve(monkeypatch, fake_generate(calls, garbage_first={"intent"}))
    payload = payload_for(target)
    row = {"conflict": False, "checks": {"total": 2, "failed": 0, "pending": 0, "passed": 2}}
    result = asyncio.run(cards.generate_all(target, payload, row))
    by_id = {c["id"]: c for c in result["cards"]}
    assert result["stale"] is False and result["head_sha"] == target.head_sha
    assert calls.count("intent") == 2  # one repair round after the garbage reply
    assert calls[-1] == "questions"
    intent = by_id["intent"]["data"]
    assert intent["claims"][1]["evidence"] == ["docs/notes.md"]
    assert intent["claims"][1]["evidence_missing"] == ["docs/missing.md"]
    assert [u["path"] for u in intent["unclaimed"]] == ["src/duckterm/server.py"]
    assert intent["verdict"] == "3 claims · 1 not in diff · 1 unclaimed"
    walk = by_id["walkthrough"]["data"]
    assert [(s["path"], s["hunk"], s["bucket"]) for s in walk["stops"]] == [
        ("src/duckterm/persistence/db.py", 1, "high"),
        ("src/duckterm/server.py", 1, "read"),
    ]
    assert walk["verdict"].startswith("2 stops · about 1 min")
    risk = by_id["risk"]["data"]
    assert risk["reasons"] == [{"path": "src/duckterm/persistence/db.py", "reason": "Schema drift"}]
    assert risk["counts"] == {"high": 1, "read": 1, "skim": 1}
    assert {f["path"]: f["lines"] for f in risk["files"]}["docs/notes.md"] == 2
    behaviour = by_id["behaviour"]["data"]
    assert behaviour["verdict"] == "1 changes · 1 high regression risk"
    release = by_id["release"]["data"]
    assert release["low_risk"] == "false"
    assert "Schema or data path" in release["condition"]
    assert {c["name"]: c["status"] for c in release["checks"]}["CI"] == "pass"
    questions = by_id["questions"]["data"]
    assert questions["items"][0]["paths"] == ["src/duckterm/server.py"]
    blast = by_id["blast"]
    assert blast["status"] == "ready"
    assert blast["data"]["model_status"] == "ready"
    assert [r["symbol"] for r in blast["data"]["risks"]] == ["route"]
    assert {r["symbol"]: r["change"] for r in blast["data"]["symbols"]} == {"route": "changed"}
    assert by_id["author"]["status"] == "omitted"
    assert by_id["author"]["reason"].startswith("No session on this computer owns")
    # Cached: a second load reads the file, no model call.
    assert cards.load(target)["cards"][0]["status"] == "ready"
    saved = json.loads((target.directory / cards.FILE).read_text())
    assert saved["head_sha"] == target.head_sha


def test_failed_cards_are_omitted_with_the_reason_and_questions_use_what_is_ready(
    target: workspace.Target, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []
    serve(monkeypatch, fake_generate(calls, fail={"intent", "behaviour"}))
    result = asyncio.run(cards.generate_all(target, payload_for(target), None))
    by_id = {c["id"]: c for c in result["cards"]}
    assert by_id["intent"] == {**by_id["intent"], "status": "omitted", "reason": "provider down"}
    assert by_id["behaviour"]["status"] == "omitted"
    assert by_id["walkthrough"]["status"] == "ready"
    assert by_id["questions"]["status"] == "ready"
    release = by_id["release"]["data"]
    assert {c["name"]: c["status"] for c in release["checks"]}["CI"] == "conditional"
    assert {c["name"]: c["status"] for c in release["checks"]}["Conflict with base"] == "unknown"


def test_no_provider_omits_every_model_card(
    target: workspace.Target, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(backend, "ready", lambda: False)
    result = asyncio.run(cards.generate_all(target, payload_for(target), None))
    by_id = {c["id"]: c for c in result["cards"]}
    assert by_id["intent"]["status"] == "omitted"
    assert "No Claude Code or Codex CLI" in by_id["intent"]["reason"]
    assert by_id["release"]["status"] == "ready"
    assert by_id["questions"]["status"] == "omitted"  # the questions card needs the model too


def test_stale_head_marks_cards_for_regeneration(
    target: workspace.Target, monkeypatch: pytest.MonkeyPatch
) -> None:
    serve(monkeypatch, fake_generate([]))
    asyncio.run(cards.generate_all(target, payload_for(target), None))
    repo = Path(target.repo)
    run(repo, "checkout", "-q", "duckterm/feature")
    write(repo, "docs/notes.md", "# Notes\n\nEven more.\n")
    commit(repo, "more docs")
    run(repo, "checkout", "-q", "main")
    lists._cache.clear()
    moved = workspace.resolve(target.repo, "duckterm/feature")
    loaded = cards.load(moved)
    assert loaded["stale"] is True
    assert {c["status"] for c in loaded["cards"] if c["id"] not in cards.LATER} == {"stale"}


def test_release_impact_is_low_risk_only_inside_the_rule(target: workspace.Target) -> None:
    ctx = cards.context(target, payload_for(target))
    safe_ctx = {**ctx, "paths": ["src/duckterm/memory.py", "web/src/App.tsx"], "files": []}
    green = {"conflict": False, "checks": {"total": 1, "failed": 0, "pending": 0, "passed": 1}}
    assert cards.release_impact(target, safe_ctx, green)["low_risk"] == "true"
    pending = {"conflict": False, "checks": {"total": 1, "failed": 0, "pending": 1, "passed": 0}}
    verdict = cards.release_impact(target, safe_ctx, pending)
    assert verdict["low_risk"] == "conditional" and "still running" in verdict["condition"]
    conflict = cards.release_impact(target, safe_ctx, {"conflict": True, "checks": green["checks"]})
    assert conflict["low_risk"] == "false" and "Conflict with base" in conflict["condition"]
    security = cards.release_impact(
        target, {**safe_ctx, "paths": ["src/duckterm/connectors.py"]}, green
    )
    assert security["low_risk"] == "false" and "Security" in security["condition"]


def test_excerpt_puts_high_risk_files_first_and_truncates_by_budget(
    target: workspace.Target, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = payload_for(target)
    text = cards.excerpt(payload)
    assert (
        text.index("persistence/db.py [high]")
        < text.index("server.py [read]")
        < text.index("notes.md [skim]")
    )
    assert "+TABLES = ['a']" in text
    monkeypatch.setattr(cards, "TOTAL_BUDGET", 300)
    short = cards.excerpt(payload)
    assert "(not shown)" in short


def test_chat_cites_paths_from_the_diff_and_fetches_requested_files_once(
    target: workspace.Target, monkeypatch: pytest.MonkeyPatch
) -> None:
    prompts: list[str] = []

    async def generate(
        harness: str, model: str, prompt: str, schema: Any = None, *, user_settings: bool = False
    ) -> str:
        prompts.append(prompt)
        if len(prompts) == 1:
            return json.dumps(
                {
                    "answer": "Need the file.",
                    "citations": [],
                    "needs_files": ["src/duckterm/server.py", "ghost.py"],
                }
            )
        return json.dumps(
            {
                "answer": (
                    "The route now returns 2 (src/duckterm/server.py:2); "
                    "see also docs/notes.md:3 and ghost.py:1."
                ),
                "citations": [
                    {"path": "src/duckterm/server.py", "line": 2},
                    {"path": "ghost.py", "line": 1},
                ],
                "needs_files": ["src/duckterm/server.py"],
            }
        )

    serve(monkeypatch, generate)
    entry = asyncio.run(
        chat.ask(
            target,
            payload_for(target),
            "What changed?",
            hunk="src/duckterm/server.py @@ def route",
            digest="Working on routes",
        )
    )
    assert len(prompts) == 2
    assert "# Files you asked for" in prompts[1] and "def route():" in prompts[1]
    assert "The authoring session's latest digest" in prompts[1]
    assert entry["citations"] == [
        {"path": "src/duckterm/server.py", "line": 2},
        {"path": "docs/notes.md", "line": 3},
    ]
    assert entry["files"] == ["src/duckterm/server.py"]
    assert chat.transcript(target)[-1]["q"] == "What changed?"
    # The next question carries the earlier exchange.
    asyncio.run(chat.ask(target, payload_for(target), "And why?", hunk=None, digest=None))
    assert "Earlier in this conversation" in prompts[2] and "What changed?" in prompts[2]


def test_draft_review_groups_open_questions_by_kind(
    target: workspace.Target, monkeypatch: pytest.MonkeyPatch
) -> None:
    with pytest.raises(APIError) as not_ready:
        chat.draft_review(target)
    assert not_ready.value.status == 409
    serve(monkeypatch, fake_generate([]))
    asyncio.run(cards.generate_all(target, payload_for(target), None))
    body = chat.draft_review(target)
    assert body.startswith(f"Review of duckterm/feature at {target.head_sha[:7]} from DuckTerm.")
    assert "**Not shown in the diff**\n- Where are the tests? (src/duckterm/server.py)" in body
    assert "**Needs a hand test**\n- Run it" in body
    chat.record_sent(target, "questions", "s1", "m1")
    saved = store.load(target.directory, cards.FILE)
    assert saved["cards"]["questions"]["sent"] == [
        {**saved["cards"]["questions"]["sent"][0], "session": "s1", "message_id": "m1"}
    ]


def test_planted_trailer_and_truthy_matcher_are_caught(
    target: workspace.Target, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The spec's done-when for build step 4."""
    repo = Path(target.repo)
    run(repo, "checkout", "-q", "duckterm/feature")
    write(
        repo,
        "web/src/review/Thing.test.tsx",
        'it("renders", () => {\n  expect(render()).toBeTruthy();\n});\n',
    )
    write(repo, "src/duckterm/review/thing.py", "import requests\n\nX = 1\n")
    run(repo, "add", "-A")
    run(
        repo,
        "commit",
        "-q",
        "-m",
        "Add thing\n\nCo-Authored-By: Claude <noreply@anthropic.com>",
    )
    run(repo, "checkout", "-q", "main")
    lists._cache.clear()
    moved = workspace.resolve(target.repo, "duckterm/feature")
    monkeypatch.setattr(backend, "ready", lambda: False)
    result = asyncio.run(cards.generate_all(moved, payload_for(moved), None))
    by_id = {c["id"]: c for c in result["cards"]}
    tests = by_id["tests"]
    assert tests["status"] == "ready"
    assert tests["data"]["model_status"] == "omitted"
    assert [(f["path"], f["kind"]) for f in tests["data"]["slop"]] == [
        ("web/src/review/Thing.test.tsx", "toBeTruthy")
    ]
    assert tests["data"]["verdict"] == "1 slop · no coverage report"
    guard = by_id["guardrails"]["data"]
    rows = {c["rule"]: c for c in guard["checks"]}
    assert rows["No Co-Authored-By or generated-with trailers"]["status"] == "finding"
    assert rows["No Co-Authored-By or generated-with trailers"]["items"][0]["text"].startswith(
        "Co-Authored-By"
    )
    assert rows["Commit messages: problem / fix / how-tested"]["status"] == "finding"
    assert rows["Server stays stdlib-only"]["items"][0]["text"] == "import requests"
    assert rows["Docs ride with code"]["status"] == "pass"
    assert guard["verdict"] == "3 findings"
    assert guard["model_status"] == "omitted"


def test_tests_card_merges_model_coverage_with_facts(
    target: workspace.Target, monkeypatch: pytest.MonkeyPatch
) -> None:
    REPLIES["tests"] = {
        "coverage": [
            {"path": "src/duckterm/server.py", "tests": [], "covered": "yes", "note": "n"},
            {"path": "ghost.py", "tests": [], "covered": "no", "note": ""},
        ],
        "gaps": ["No test for the route change"],
        "suggested": [{"name": "test_route", "path": "tests/unit/test_server.py", "body": "x"}],
    }
    REPLIES["guardrails"] = {
        "findings": [
            {"rule": "Comment slop", "status": "pass", "location": "", "note": "none"},
            {
                "rule": "Over-engineering",
                "status": "finding",
                "location": "src/duckterm/server.py:1",
                "note": "wrapper",
            },
            {"rule": "bad", "status": "maybe", "location": "", "note": ""},
        ]
    }
    serve(monkeypatch, fake_generate([]))
    result = asyncio.run(cards.generate_all(target, payload_for(target), None))
    by_id = {c["id"]: c for c in result["cards"]}
    tests = by_id["tests"]["data"]
    assert tests["model_status"] == "ready"
    assert [(r["path"], r["covered"]) for r in tests["coverage"]] == [
        ("src/duckterm/server.py", "no")
    ]
    assert tests["verdict"] == "0 slop · 1 uncovered · no coverage report"
    assert tests["existing_tests"] == {
        "src/duckterm/persistence/db.py": [],
        "src/duckterm/server.py": [],
    }
    guard = by_id["guardrails"]["data"]
    assert [f["rule"] for f in guard["findings"]] == ["Comment slop", "Over-engineering"]
    assert guard["verdict"] == "2 findings"  # the model finding plus the fixture commit subject
