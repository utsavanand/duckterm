import subprocess
import time
from pathlib import Path

import pytest

from duckterm.review import checks


def file(path: str, added: list[str], start: int = 1) -> dict:  # type: ignore[type-arg]
    return {
        "path": path,
        "binary": False,
        "hunks": [
            {
                "new_start": start,
                "lines": [{"kind": "+", "text": line} for line in added],
            }
        ],
    }


def test_test_slop_catches_matchers_and_assertion_free_tests() -> None:
    ts = file(
        "web/src/review/PrList.test.tsx",
        [
            'it("renders", () => {',
            "  const x = render(<A />);",
            "  expect(x).toBeTruthy();",
            "});",
            'it("does nothing", () => {',
            "  render(<A />);",
            "});",
            'it("checks", () => { expect(1).toBe(1); });',
        ],
        start=10,
    )
    py = file(
        "tests/unit/test_x.py",
        [
            "def test_exists():",
            "    value = make()",
            "    assert value is not None",
            "def test_real():",
            "    assert make() == 1",
        ],
    )
    findings = checks.test_slop([ts, py, file("src/duckterm/x.py", ["assert True"])])
    assert [(f["path"].split("/")[-1], f["line"], f["kind"]) for f in findings] == [
        ("PrList.test.tsx", 12, "toBeTruthy"),
        ("PrList.test.tsx", 14, "no assertion"),
        ("test_x.py", 3, "assert not None"),
    ]


def test_commit_messages_flag_format_and_trailers() -> None:
    commits = [
        {"sha": "a" * 40, "subject": "Problem / fix / how-tested", "body": ""},
        {
            "sha": "b" * 40,
            "subject": "Fix bug",
            "body": "Details\n\nCo-Authored-By: Claude <noreply@anthropic.com>",
        },
        {"sha": "c" * 40, "subject": "Merge branch main", "body": ""},
    ]
    findings = checks.commit_messages(commits)
    assert [(f["sha"], f["kind"]) for f in findings] == [
        ("bbbbbbb", "format"),
        ("bbbbbbb", "trailer"),
    ]
    assert findings[1]["text"].startswith("Co-Authored-By")


def test_stdlib_imports_flag_only_new_third_party_modules_under_src() -> None:
    files = [
        file(
            "src/duckterm/review/x.py",
            [
                "import json",
                "from duckterm.review import git",
                "import requests",
                "from pathlib import Path",
            ],
        ),
        file("tests/unit/test_y.py", ["import pytest"]),
        file("scripts/tool.py", ["import yaml"]),
    ]
    findings = checks.stdlib_imports(files)
    assert [(f["path"], f["line"], f["text"]) for f in findings] == [
        ("src/duckterm/review/x.py", 3, "import requests")
    ]


def test_retired_terms_in_docs_and_ui_only() -> None:
    files = [
        file("docs/spec.md", ["The overlay shows", "A test suite runs", "The skill kit suite"]),
        file("web/src/App.tsx", ["<p>meta-harness</p>"]),
        file("src/duckterm/x.py", ["overlay = 1"]),
    ]
    findings = checks.retired_terms(files)
    assert [(f["path"], f["line"], f["text"]) for f in findings] == [
        ("docs/spec.md", 1, "overlay"),
        ("docs/spec.md", 3, "suite"),
        ("web/src/App.tsx", 1, "meta-harness"),
    ]


def test_shell_habits_and_docs_only() -> None:
    files = [
        file(
            "scripts/release.sh",
            ["scripts/gate.sh | tail -5", "git add -A", "git add src/x.py", "pytest tests -q"],
        )
    ]
    findings = checks.shell_habits(files)
    assert [(f["line"], f["kind"]) for f in findings] == [(1, "piped gate"), (2, "git add -A")]
    assert checks.docs_only(["docs/a.md", "README.md"]) is True
    assert checks.docs_only(["docs/a.md", "src/x.py"]) is False
    assert checks.docs_only([]) is False


def test_existing_tests_match_changed_modules(git_repo: Path) -> None:
    for name in (
        "src/duckterm/transfers.py",
        "tests/unit/test_transfers.py",
        "tests/unit/review/test_git.py",
        "web/src/PrList.tsx",
        "web/src/review/PrList.test.tsx",
        "src/duckterm/cli.py",
    ):
        (git_repo / name).parent.mkdir(parents=True, exist_ok=True)
        (git_repo / name).write_text("x\n")
    subprocess.run(["git", "-C", str(git_repo), "add", "-A"], check=True)
    subprocess.run(
        ["git", "-C", str(git_repo), "-c", "commit.gpgsign=false", "commit", "-q", "-m", "tree"],
        check=True,
    )
    found = checks.existing_tests(
        str(git_repo),
        "HEAD",
        [
            "src/duckterm/transfers.py",
            "web/src/PrList.tsx",
            "src/duckterm/cli.py",
            "tests/unit/test_transfers.py",
        ],
    )
    assert found == {
        "src/duckterm/transfers.py": ["tests/unit/test_transfers.py"],
        "web/src/PrList.tsx": ["web/src/review/PrList.test.tsx"],
        "src/duckterm/cli.py": [],
    }


def test_gate_report_only_when_newer_than_the_head(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    log = tmp_path / "gate.log"
    log.write_text("==> pytest\n412 passed in 30.1s\n==> GATE PASSED (log: x)\n")
    monkeypatch.setattr(checks, "GATE_LOG", log)
    report = checks.gate_report(int(time.time()) - 60)
    assert report is not None and report["passed"] is True
    assert report["summary"] == "412 passed in 30.1s"
    assert checks.gate_report(int(time.time()) + 3600) is None
    monkeypatch.setattr(checks, "GATE_LOG", tmp_path / "missing.log")
    assert checks.gate_report(None) is None


def test_coverage_report_reads_pytest_cov_and_vitest_summaries(tmp_path: Path) -> None:
    assert checks.coverage_report(str(tmp_path), ["src/duckterm/x.py"]) is None
    cls = '<class filename="duckterm/x.py" line-rate="0.8125"/>'
    (tmp_path / "coverage.xml").write_text(
        f"<coverage><packages><package><classes>{cls}</classes></package></packages></coverage>"
    )
    (tmp_path / "web" / "coverage").mkdir(parents=True)
    (tmp_path / "web" / "coverage" / "coverage-summary.json").write_text(
        '{"total": {"lines": {"pct": 50}}, "/abs/web/src/App.tsx": {"lines": {"pct": 91.5}}}'
    )
    report = checks.coverage_report(
        str(tmp_path), ["src/duckterm/x.py", "web/src/App.tsx", "docs/a.md"]
    )
    assert report == {
        "files": {"src/duckterm/x.py": 81, "web/src/App.tsx": 91.5},
        "source": "coverage.xml",
    }
