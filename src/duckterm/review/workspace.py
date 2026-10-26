"""The review workspace for one pull request or branch: resolve the ref,
describe the change against its base, and serve the diff with buckets and
viewed state (spec §4)."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from duckterm.review import buckets, diff, git, lists, store


class ReviewError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


@dataclass
class Target:
    repo: str
    ref: str
    slug: str | None
    branch: str
    pr: dict[str, Any] | None
    base: str
    base_ref: str
    head_ref: str
    head_sha: str
    base_sha: str

    @property
    def directory(self) -> Path:
        return store.review_dir(self.repo, self.slug, self.ref)


def resolve(repo: str, ref: str, *, refresh: bool = False) -> Target:
    if not git.is_repo(repo):
        raise ReviewError(404, "That repository is not available on this computer")
    state = lists._repo_state(repo, [], refresh=refresh)
    pr: dict[str, Any] | None
    if ref.isdigit():
        pr = next((p for p in state["pulls"] if p["number"] == int(ref)), None)
        if pr is None:
            raise ReviewError(404, f"Pull request #{ref} is not in this repository's recent pulls")
        branch = pr["head"]
    else:
        branch = ref
        pr = lists._pull_for(state["pulls"], branch)
    head_ref = None
    for candidate in (f"refs/heads/{branch}", f"refs/remotes/origin/{branch}"):
        if git.ref_exists(repo, candidate):
            head_ref = candidate
            break
    if head_ref is None and pr and git.resolve(repo, pr["head_sha"]):
        head_ref = pr["head_sha"]
    if head_ref is None and state["heads"] is not None and branch in state["heads"]:
        git.fetch(repo, [branch], tags=False)
        if git.ref_exists(repo, f"refs/remotes/origin/{branch}"):
            head_ref = f"refs/remotes/origin/{branch}"
    if head_ref is None:
        raise ReviewError(404, f"Branch {branch} is not available locally or on origin")
    head_sha = git.resolve(repo, head_ref) or ""
    base = pr["base"] if pr else state["default"]
    base_ref = git.base_ref(repo, base)
    base_sha = git.merge_base(repo, base_ref, head_ref)
    if not base_sha:
        raise ReviewError(409, f"{branch} and {base} share no history")
    return Target(
        repo, ref, state["slug"], branch, pr, base, base_ref, head_ref, head_sha, base_sha
    )


def header(target: Target) -> dict[str, Any]:
    state = lists._repo_state(target.repo, [], refresh=False)
    row = lists._row(
        target.repo, state, branch=target.branch, pr=target.pr, session=None, local=True
    )
    commits = int(
        git.run(target.repo, "rev-list", "--count", f"{target.base_sha}..{target.head_ref}")
    )
    heads = store.load(target.directory, store.VIEWED).get("heads") or []
    return {
        "repo": target.repo,
        "repo_name": Path(target.repo).name,
        "github": target.slug,
        "ref": target.ref,
        "branch": target.branch,
        "base": target.base,
        "base_sha": target.base_sha,
        "head_sha": target.head_sha,
        "commits": commits,
        "pr": target.pr,
        "row": row,
        "previous_head": store.previous_head(heads, target.head_sha),
    }


def _hotspots(repo: str, head_ref: str) -> Counter[str]:
    try:
        out = git.run(repo, "log", "--since=30.days", "--name-only", "--format=", head_ref)
    except git.ReviewGitError:
        return Counter()
    return Counter(line for line in out.splitlines() if line)


def _changed_since(repo: str, since: str | None, head_ref: str) -> set[str] | None:
    if not since or git.resolve(repo, since) is None:
        return None
    try:
        out = git.run(repo, "diff", "--name-only", "--find-renames", since, head_ref)
    except git.ReviewGitError:
        return None
    return set(out.splitlines())


def diff_payload(target: Target, *, since: str | None, whitespace: bool) -> dict[str, Any]:
    args = (
        "diff",
        "--no-ext-diff",
        "--no-color",
        "--find-renames",
        target.base_sha,
        target.head_ref,
    )
    full = diff.parse(git.run(target.repo, *args, timeout=60))
    loose = diff.parse(git.run(target.repo, *args[:1], "-w", *args[1:], timeout=60))
    loose_by_path = {f.path: f for f in loose}
    hotspots = _hotspots(target.repo, target.head_ref)
    heads = store.record_head(target.directory, target.head_sha)
    since = since or store.previous_head(heads, target.head_sha)
    changed_since = _changed_since(target.repo, since, target.head_ref)
    state = store.load(target.directory, store.VIEWED).get("files") or {}
    files: list[dict[str, Any]] = []
    for file in full:
        loose_file = loose_by_path.get(file.path) or diff.FileDiff(
            path=file.path, status=file.status
        )
        whitespace_only = bool(file.hunks) and not loose_file.hunks
        bucket, rules = buckets.classify(
            loose_file if loose_file.hunks else file,
            whitespace_only=whitespace_only,
            hotspot_commits=hotspots.get(file.path, 0),
        )
        digest = diff.hunk_hash(loose_file)
        saved = state.get(file.path) or {}
        shown = loose_file if whitespace else file
        entry = diff.to_json(shown)
        entry.update(
            {
                "additions": file.additions,
                "deletions": file.deletions,
                "bucket": saved.get("bucket") or bucket,
                "bucket_rule": bucket,
                "rules": rules,
                "hash": digest,
                "viewed": bool(saved.get("viewed")) and saved.get("hash") == digest,
                "stale": bool(saved.get("viewed")) and saved.get("hash") != digest,
                "changed_since": file.path in changed_since if changed_since is not None else None,
                "whitespace_only": whitespace_only,
            }
        )
        files.append(entry)
    files.sort(key=lambda f: (buckets.ORDER[f["bucket"]], f["path"]))
    counts = Counter(f["bucket"] for f in files)
    return {
        "base_sha": target.base_sha,
        "head_sha": target.head_sha,
        "since": since,
        "whitespace": whitespace,
        "files": files,
        "counts": {
            "high": counts.get("high", 0),
            "read": counts.get("read", 0),
            "skim": counts.get("skim", 0),
            "viewed": sum(1 for f in files if f["viewed"]),
            "changed_since": sum(1 for f in files if f["changed_since"]),
        },
    }


def mark_viewed(target: Target, body: dict[str, Any]) -> dict[str, Any]:
    path = body.get("path")
    digest = body.get("hash")
    if not isinstance(path, str) or not isinstance(digest, str):
        raise ReviewError(400, "path and hash are required")
    viewed = body.get("viewed")
    bucket = body.get("bucket")
    if viewed is not None and not isinstance(viewed, bool):
        raise ReviewError(400, "viewed must be true or false")
    if bucket is not None and bucket not in ("", "high", "read", "skim"):
        raise ReviewError(400, "bucket must be high, read or skim")
    return store.mark(target.directory, path, digest, viewed=viewed, bucket=bucket)
