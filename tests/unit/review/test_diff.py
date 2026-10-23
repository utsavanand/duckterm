import subprocess
from pathlib import Path

from duckterm.review import buckets, diff

NO_SIGNING = ["-c", "tag.gpgsign=false", "-c", "commit.gpgsign=false"]


def run(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *NO_SIGNING, "-C", str(repo), *args], check=True, capture_output=True, text=True
    ).stdout


def commit(repo: Path, message: str) -> None:
    run(repo, "add", "-A")
    run(repo, "commit", "-q", "-m", message)


def test_parse_handles_add_modify_rename_binary_and_no_newline(git_repo: Path) -> None:
    (git_repo / "pkg").mkdir()
    (git_repo / "pkg" / "a.py").write_text("def a():\n    return 1\n")
    (git_repo / "keep.txt").write_text("same\n")
    (git_repo / "logo.png").write_bytes(b"\x89PNG\r\n\x1a\n\x00")
    commit(git_repo, "base")
    base = run(git_repo, "rev-parse", "HEAD").strip()
    run(git_repo, "mv", "keep.txt", "kept.txt")
    (git_repo / "pkg" / "a.py").write_text("def a():\n    return 2\n\n\ndef b():\n    return 3")
    (git_repo / "new.md").write_text("# New\n")
    (git_repo / "logo.png").write_bytes(b"\x89PNG\r\n\x1a\n\x01")
    (git_repo / "README.md").unlink()
    commit(git_repo, "change")

    files = {f.path: f for f in diff.parse(run(git_repo, "diff", "--find-renames", base, "HEAD"))}
    assert set(files) == {"pkg/a.py", "kept.txt", "new.md", "logo.png", "README.md"}
    assert files["kept.txt"].status == "renamed"
    assert files["kept.txt"].old_path == "keep.txt"
    assert files["kept.txt"].hunks == []
    assert files["new.md"].status == "added" and files["new.md"].additions == 1
    assert files["README.md"].status == "deleted" and files["README.md"].deletions == 1
    assert files["logo.png"].binary is True
    a = files["pkg/a.py"]
    assert a.status == "modified"
    assert (a.additions, a.deletions) == (5, 1)
    assert a.hunks[0].lines[-1][0] == "\\"  # "No newline at end of file" marker kept
    rendered = diff.to_json(a)
    assert rendered["hunks"][0]["lines"][1] == {"kind": "-", "text": "    return 1"}
    assert rendered["truncated"] is False


def unified(old: str, new: str) -> str:
    head = "diff --git a/x.py b/x.py\n--- a/x.py\n+++ b/x.py\n@@ -1,2 +1,2 @@\n"
    return f"{head} def f():\n-{old}\n+{new}\n"


def test_hash_ignores_whitespace_but_not_content() -> None:
    before = diff.parse(unified("    return 1", "    return 2"))
    reindented = diff.parse(unified("  return 1", "\treturn   2"))
    changed = diff.parse(unified("    return 1", "    return 3"))
    assert diff.hunk_hash(before[0]) == diff.hunk_hash(reindented[0])
    assert diff.hunk_hash(before[0]) != diff.hunk_hash(changed[0])


def test_large_files_keep_only_the_first_400_lines() -> None:
    body = "".join(f"+line {i}\n" for i in range(2500))
    head = "diff --git a/big.txt b/big.txt\nnew file mode 100644\n--- /dev/null\n+++ b/big.txt\n"
    files = diff.parse(f"{head}@@ -0,0 +1,2500 @@\n{body}")
    rendered = diff.to_json(files[0])
    assert rendered["truncated"] is True
    assert rendered["total_lines"] == 2500
    assert sum(len(h["lines"]) for h in rendered["hunks"]) == diff.HEAD_LINES


def file(
    path: str,
    added: list[str],
    removed: list[str] | None = None,
    status: str = "modified",
    binary: bool = False,
) -> diff.FileDiff:
    hunk = diff.Hunk(header="", old_start=1, old_lines=1, new_start=1, new_lines=1)
    hunk.lines = [("-", line) for line in removed or []] + [("+", line) for line in added]
    return diff.FileDiff(
        path=path, status=status, binary=binary, hunks=[hunk] if (added or removed) else []
    )


def test_every_bucket_rule_fires_on_its_trigger() -> None:
    cases = [
        (file("src/duckterm/persistence/history.py", ["x = 1"]), "high", "schema"),
        (file("src/duckterm/server.py", ["CREATE TABLE foo (id)"]), "high", "schema"),
        (file("src/duckterm/core/relay.py", ["with self._lock:"]), "high", "locks"),
        (file("src/duckterm/server.py", ['Route("GET", "/x", handler),']), "high", "routes"),
        (file("web/src/api.ts", ["headers: authHeaders(),"]), "high", "routes"),
        (file("scripts/gate.sh", ["set -e"]), "high", "ops"),
        (file("mac/build.sh", ["xcodebuild"]), "high", "ops"),
        (file(".github/workflows/ci.yml", ["on: push"]), "high", "ops"),
        (
            file("src/duckterm/review/new.py", [f"line {i}" for i in range(101)], status="added"),
            "high",
            "large-new",
        ),
        (
            file("tests/unit/test_new.py", [f"line {i}" for i in range(101)], status="added"),
            "read",
            "tests",
        ),
        (file("src/duckterm/memory.py", ["x = 1"]), "read", "source"),
        (file("web/src/App.tsx", ["const x = 1;"]), "read", "source"),
        (file("web/src/App.test.tsx", ["it('x')"]), "read", "tests"),
        (file("docs/spec.md", ["# Title"]), "skim", "docs"),
        (file("web/package-lock.json", ['"version": "2"']), "skim", "lockfile"),
        (
            file("web/package.json", ['  "version": "0.4.174",'], ['  "version": "0.4.173",']),
            "skim",
            "version-bump",
        ),
        (file("assets/logo.png", [], binary=True), "skim", "binary"),
        (file("old.py", [], status="renamed"), "skim", "rename-only"),
        (
            file("src/duckterm/cli.py", ['print("hello world")'], ['print("hello")']),
            "skim",
            "strings-only",
        ),
        (file("mac/notes.md", ["x"]), "skim", "docs"),
    ]
    for candidate, bucket, rule in cases:
        got, rules = buckets.classify(candidate, whitespace_only=False)
        assert got == bucket, (candidate.path, got, rules)
        assert rule in {r["id"] for r in rules}, (candidate.path, rules)


def test_hotspot_whitespace_and_high_override_rules() -> None:
    hot, rules = buckets.classify(
        file("src/duckterm/memory.py", ["x = 1"]), whitespace_only=False, hotspot_commits=11
    )
    assert hot == "high" and "hotspot" in {r["id"] for r in rules}
    ws, rules = buckets.classify(
        file("src/duckterm/persistence/history.py", []), whitespace_only=True
    )
    assert ws == "skim" and "whitespace-only" in {r["id"] for r in rules}
    strings_in_schema, _ = buckets.classify(
        file(
            "src/duckterm/persistence/history.py", ["CREATE TABLE b (id)"], ["CREATE TABLE a (id)"]
        ),
        whitespace_only=False,
    )
    assert strings_in_schema == "high"
    multi, rules = buckets.classify(file("mac/build.sh", ["x"]), whitespace_only=False)
    assert multi == "high" and {r["bucket"] for r in rules} == {"high", "read"}
