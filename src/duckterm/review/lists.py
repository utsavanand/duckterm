"""Pull request rows for a session or a folder.

A row is a branch a session's worktree is on, joined with the pull request
whose head is that branch. A session's branches are the one its checkout is
on plus every branch it pushed or opened a PR for in the last PUSH_DAYS days,
read from its recorded commands: most PRs are pushed from short-lived
worktrees no session is registered on, so the checkout alone showed at most
one PR per session. A branch belongs to the first session that pushed it, so
a later merge pushed by the release session does not move it. A folder lists
only its own sessions' branches, never other pull requests in the same
repository (owner ruling, 2026-10-26).
Git facts come from the local repository; PR facts come from GitHub and are
cached per repository for CACHE_SECONDS, so a list that polls every minute
costs one page of pulls plus one check-runs and one reviews call per open PR.
"""

from __future__ import annotations

import os
import re
import shlex
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from duckterm.persistence.folder_chats import within
from duckterm.review import git, github, status

CACHE_SECONDS = 60
PUSH_DAYS = 14

_BRANCH = re.compile(r"[A-Za-z0-9._][A-Za-z0-9._/-]*")
_ASSIGNMENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*=[^$`]*")
_VARIABLE = re.compile(r"\$(?:\{([A-Za-z_][A-Za-z0-9_]*)\}|([A-Za-z_][A-Za-z0-9_]*))")
# git push options that take a separate value; the value is not a refspec.
_PUSH_VALUE_OPTIONS = {"-o", "--push-option", "--repo", "--receive-pack", "--exec"}

_lock = threading.Lock()
_cache: dict[str, dict[str, Any]] = {}
# Rows that can no longer change, by (repo, PR number, head sha): closed PRs,
# and merged ones once a release tag contains them. A folder lists dozens of
# these from its push history, and rebuilding them each poll cost seconds.
_settled: dict[tuple[str, int, str], dict[str, Any]] = {}


def _iso_to_epoch(value: Any) -> int | None:
    if not value:
        return None
    try:
        return int(datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp())
    except ValueError:
        return None


def _without_data(command: str) -> str | None:
    """`command` with heredoc bodies and comments removed: they are data (a PR
    body, a notes file), not commands. Quotes, `$(...)` and backticks are
    followed so a `<<` or `#` inside a quoted argument is left alone, and every
    heredoc opened on a line is consumed, in order, after that line. None when
    the syntax cannot be read with certainty (an unclosed quote or group, a
    heredoc without a delimiter); a heredoc never closed runs to the end."""
    out: list[str] = []
    pending: list[tuple[str, bool]] = []
    # Frames: "top" and "(" (and "`") are shell syntax; '"' is a double-quoted string.
    stack = ["top"]
    i, n = 0, len(command)
    while i < n:
        c = command[i]
        syntax = stack[-1] != '"'
        if c == "\\":
            out.append(command[i : i + 2])
            i += 2
            continue
        if c == "$" and command.startswith("$(", i):
            stack.append("(")
            out.append("$(")
            i += 2
            continue
        if c == "`":
            if stack[-1] == "`":
                stack.pop()
            else:
                stack.append("`")
        elif not syntax:
            if c == '"':
                stack.pop()
        elif c == '"':
            stack.append('"')
        elif c == "'":
            end = command.find("'", i + 1)
            if end < 0:
                return None
            out.append(command[i : end + 1])
            i = end + 1
            continue
        elif c == "(":
            stack.append("(")
        elif c == ")":
            if stack[-1] != "(":
                return None
            stack.pop()
        elif c == "#" and (i == 0 or command[i - 1] in " \t\n;&|()"):
            end = command.find("\n", i)
            i = n if end < 0 else end
            continue
        elif command.startswith("<<<", i):
            out.append("<<<")
            i += 3
            continue
        elif command.startswith("<<", i):
            j = i + 2
            strip = command.startswith("-", j)
            j += strip
            while j < n and command[j] in " \t":
                j += 1
            delimiter, j = _heredoc_word(command, j)
            if not delimiter:
                return None
            pending.append((delimiter, strip))
            out.append(command[i:j])
            i = j
            continue
        elif c == "\n" and pending:
            out.append("\n")
            i += 1
            for delimiter, strip in pending:
                while True:
                    if i >= n:
                        return "".join(out)
                    end = command.find("\n", i)
                    end = n if end < 0 else end
                    line = command[i:end]
                    i = end + 1
                    if (line.lstrip("\t") if strip else line) == delimiter:
                        break
            pending = []
            continue
        out.append(c)
        i += 1
    return "".join(out) if stack == ["top"] else None


def _heredoc_word(command: str, i: int) -> tuple[str, int]:
    """The delimiter of a heredoc whose word starts at `i`, quotes removed,
    and the index after the word."""
    word: list[str] = []
    while i < len(command) and command[i] not in " \t\n;&|()<>":
        c = command[i]
        if c in "'\"":
            end = command.find(c, i + 1)
            if end < 0:
                return "", i
            word.append(command[i + 1 : end])
            i = end + 1
        elif c == "\\":
            word.append(command[i + 1 : i + 2])
            i += 2
        else:
            word.append(c)
            i += 1
    return "".join(word), i


def _split(script: str) -> list[list[str]] | None:
    """Shell words of each simple command in `script`, with `(` and `)`
    as one-word entries where a group or substitution opens and closes, or
    None when shlex cannot read it."""
    try:
        lexer = shlex.shlex(script, posix=True, punctuation_chars=True)
        lexer.whitespace_split = True
        words = list(lexer)
    except ValueError:
        return None
    out: list[list[str]] = []
    current: list[str] = []
    redirect = False
    for word in words:
        if redirect:
            redirect = False
        elif word and set(word) <= set("<>&") and set(word) & set("<>"):
            # `2>&1`, `> out`, `<<'EOF'`: the target is not an argument.
            if current and current[-1].isdigit():
                current.pop()
            redirect = True
        elif word and set(word) <= set(";&|()"):
            out.append(current)
            current = []
            out.extend([ch] for ch in word if ch in "()")
        else:
            current.append(word)
    out.append(current)
    return [words for words in out if words]


def _simple_commands(command: str) -> list[list[str]] | None:
    """Shell words of each simple command in a recorded command, read as one
    script so a quoted commit message spanning lines does not hide the push
    after it. None when the command cannot be read with certainty: it then
    claims nothing rather than a guessed branch."""
    script = _without_data(command)
    if script is None:
        return None
    return _split(script.replace("\\\n", " ").replace("\n", " ;\n"))


def _join(directory: str | None, path: str) -> str | None:
    """`path` taken from `directory`, or None when the shell would expand it
    or the base is unknown."""
    if not path or path[0] in "~-" or any(ch in path for ch in "$`*?["):
        return None
    if os.path.isabs(path):
        return os.path.normpath(path)
    return os.path.normpath(os.path.join(directory, path)) if directory else None


def _branch_name(ref: str) -> str | None:
    ref = ref.lstrip("+").removeprefix("refs/heads/")
    if ref == "HEAD" or ref.startswith("refs/") or not _BRANCH.fullmatch(ref):
        return None
    return ref


def _pushed(words: list[str], directory: str | None) -> tuple[str | None, list[str]]:
    """The directory a `git push` or `gh pr create` runs in and the branches
    it names. A bare `git push` names none: which branch it pushed depends on
    a checkout the record lacks. The directory is None when it cannot be
    known (`--git-dir`, `gh --repo`), so the branches go unclaimed."""
    if words[:3] == ["gh", "pr", "create"]:
        if any(w in ("-R", "--repo") or w.startswith("--repo=") for w in words):
            return None, []
        for i, word in enumerate(words):
            if word in ("--head", "-H") and i + 1 < len(words):
                name = _branch_name(words[i + 1].rsplit(":", 1)[-1])
                return directory, [name] if name else []
            if word.startswith("--head="):
                name = _branch_name(word.split("=", 1)[1].rsplit(":", 1)[-1])
                return directory, [name] if name else []
        return directory, []
    if not words or words[0] != "git":
        return directory, []
    i = 1
    while i < len(words) and words[i].startswith("-"):
        if words[i] == "-C" and i + 1 < len(words):
            directory = _join(directory, words[i + 1])
        elif words[i].startswith(("--git-dir", "--work-tree")):
            directory = None
        i += 2 if words[i] in ("-c", "-C") else 1
    if i >= len(words) or words[i] != "push":
        return directory, []
    args = words[i + 1 :]
    if any(a in ("-d", "--delete", "--tags", "--all", "--mirror", "-n", "--dry-run") for a in args):
        return directory, []
    positional: list[str] = []
    skip = False
    for arg in args:
        if skip:
            skip = False
        elif arg in _PUSH_VALUE_OPTIONS:
            skip = True
        elif not arg.startswith("-"):
            positional.append(arg)
    # `:branch` (empty source) deletes the remote branch rather than pushing it.
    names = [
        _branch_name(spec.rsplit(":", 1)[-1])
        for spec in positional[1:]
        if not spec.lstrip("+").startswith(":")
    ]
    return directory, [name for name in names if name]


def _expand(word: str, values: dict[str, str]) -> str:
    """`word` with the command's own plain assignments (`S=/tmp/x; cd $S/wt`)
    substituted. Anything else stays unexpanded, and _join refuses it."""
    return _VARIABLE.sub(lambda m: values.get(m.group(1) or m.group(2), m.group(0)), word)


def _worktree_added(words: list[str], directory: str | None) -> str | None:
    """The path a `git worktree add` creates, or None."""
    if words[:3] != ["git", "worktree", "add"]:
        return None
    skip = False
    for arg in words[3:]:
        if skip:
            skip = False
        elif arg in ("-b", "-B", "--reason"):
            skip = True
        elif not arg.startswith("-"):
            return _join(directory, arg)
    return None


def _pushes(command: str, directory: str) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    """(directory, branch) for each branch a recorded command pushes or opens
    a PR for, and (path, directory) for each worktree it adds. The directory
    starts at the recorded one and follows `cd` and `git -C`; a `cd` inside
    `( )` or `$( )` ends with the group. Once the directory is unknown
    (`cd -`, `cd $UNSET`, `popd`) nothing more is claimed."""
    commands = _simple_commands(command)
    if commands is None:
        return [], []
    here: str | None = directory or None
    groups: list[str | None] = []
    values: dict[str, str] = {}
    pushed: list[tuple[str, str]] = []
    added: list[tuple[str, str]] = []
    for words in commands:
        words = [_expand(word, values) for word in words]
        if words[0] == "export":
            words = words[1:] or [""]
        if words == ["("]:
            groups.append(here)
        elif words == [")"]:
            here = groups.pop() if groups else None
        elif all(_ASSIGNMENT.fullmatch(word) for word in words):
            for word in words:
                name, value = word.split("=", 1)
                values[name] = value
        elif words[0] in ("cd", "pushd"):
            here = _join(here, words[1]) if len(words) == 2 else None
        elif words[0] == "popd":
            here = None
        else:
            where, branches = _pushed(words, here)
            if where:
                pushed.extend((where, branch) for branch in branches)
            path = _worktree_added(words, here)
            if path and here:
                added.append((path, here))
    return pushed, added


def branch_owners(commands: list[tuple[str, int, str, str]]) -> dict[tuple[str, str], str]:
    """{(repository, branch): session key} from (session key, ts, command,
    directory) rows: the first session to push a branch, or open a PR for it,
    in that repository owns it. The repository is the one each push ran in:
    the recorded directory, followed through `cd` and `git -C`. A worktree
    removed since is mapped through the recorded `git worktree add` that made
    it. Only rows inside the PUSH_DAYS window are read, so a branch whose
    first push has aged out belongs to whoever pushed it first within the
    window. Runs in a worker: it calls git to map each directory to its
    repository."""
    roots: dict[str, str | None] = {}
    worktrees: dict[str, str] = {}

    def root(directory: str) -> str | None:
        if directory not in roots:
            try:
                roots[directory] = git.repo_root(directory)
            except git.ReviewGitError:
                made = next((w for w in worktrees if within(directory, w)), None)
                roots[directory] = root(worktrees[made]) if made else None
        return roots[directory]

    owners: dict[tuple[str, str], str] = {}
    for key, _, command, directory in sorted(commands, key=lambda row: row[1]):
        if not any(word in command for word in ("push", "pr create", "worktree add")):
            continue
        pushed, added = _pushes(command, directory)
        worktrees.update(added)
        for where, branch in pushed:
            repo = root(where)
            if repo:
                owners.setdefault((repo, branch), key)
    return owners


def _repo_state(repo: str, branches: list[str], *, refresh: bool) -> dict[str, Any]:
    """GitHub pulls and remote heads for one repository, refreshed at most
    once per CACHE_SECONDS unless the owner asked."""
    now = time.time()
    with _lock:
        entry = _cache.get(repo)
        fresh = entry is not None and now - entry["at"] < CACHE_SECONDS
        if entry is not None and not refresh and fresh:
            return entry
    slug = git.github_slug(repo)
    default = git.default_branch(repo)
    heads = git.remote_heads(repo)
    fetch_error = None
    if heads is not None:
        wanted = [b for b in dict.fromkeys([default, *branches]) if b in heads]
        fetch_error = git.fetch(repo, wanted)
    pulls: list[dict[str, Any]] = []
    error: str | None = None
    token: str | None = None
    if slug:
        token, error = github.token()
        if token:
            try:
                pulls = github.pulls(slug, token)
            except github.GitHubError as exc:
                error = str(exc)
    elif heads is None:
        error = "The remote is unreachable"
    entry = {
        "at": time.time(),
        "slug": slug,
        "default": default,
        "heads": heads,
        "fetch_error": fetch_error,
        "pulls": pulls,
        "error": error,
        "token": token,
        "checks": {},
        "reviews": {},
    }
    with _lock:
        _cache[repo] = entry
    return entry


def _checks(state: dict[str, Any], sha: str) -> dict[str, int] | None:
    if not state["token"] or not state["slug"]:
        return None
    if sha not in state["checks"]:
        try:
            state["checks"][sha] = github.check_runs(state["slug"], sha, state["token"])
        except github.GitHubError as exc:
            state["error"] = state["error"] or str(exc)
            return None
    value: dict[str, int] | None = state["checks"][sha]
    return value


def _review(state: dict[str, Any], number: int) -> str:
    if not state["token"] or not state["slug"]:
        return "none"
    if number not in state["reviews"]:
        try:
            state["reviews"][number] = github.review_state(state["slug"], number, state["token"])
        except github.GitHubError as exc:
            state["error"] = state["error"] or str(exc)
            return "none"
    value: str = state["reviews"][number]
    return value


def _pull_for(pulls: list[dict[str, Any]], branch: str) -> dict[str, Any] | None:
    matches = [p for p in pulls if p["head"] == branch]
    if not matches:
        return None
    open_ones = [p for p in matches if p["state"] == "open"]
    return (open_ones or matches)[0]


def _row(
    repo: str,
    state: dict[str, Any],
    *,
    branch: str,
    pr: dict[str, Any] | None,
    session: dict[str, str] | None,
    local: bool,
) -> dict[str, Any] | None:
    default = state["default"]
    base = pr["base"] if pr else default
    base_ref = git.base_ref(repo, base)
    heads = state["heads"]
    # Offline, the last fetch's remote-tracking ref is the best evidence of a push.
    pushed = (
        branch in heads
        if heads is not None
        else git.ref_exists(repo, f"refs/remotes/origin/{branch}")
    )
    head_ref = branch if local and git.ref_exists(repo, f"refs/heads/{branch}") else None
    if head_ref is None and pushed and git.ref_exists(repo, f"refs/remotes/origin/{branch}"):
        head_ref = f"origin/{branch}"
    ahead = behind = None
    size = None
    conflict = None
    tip_time = None
    if head_ref and git.ref_exists(repo, base_ref):
        ahead, behind = git.ahead_behind(repo, base_ref, head_ref)
        size = git.shortstat(repo, base_ref, head_ref)
        tip_time = git.commit_time(repo, head_ref)
        if pr is None or pr["state"] == "open":
            conflict = git.conflicts(repo, base_ref, head_ref) if ahead else False
    if pr is None and not pushed and not ahead:
        return None
    unpushed = 0
    if head_ref and pushed and git.ref_exists(repo, f"refs/remotes/origin/{branch}"):
        unpushed, _ = git.ahead_behind(repo, f"origin/{branch}", head_ref)
    checks = _checks(state, pr["head_sha"]) if pr and pr["state"] == "open" else None
    review = _review(state, pr["number"]) if pr and pr["state"] == "open" else "none"
    version = None
    if pr and pr["state"] == "merged" and pr.get("merge_commit_sha"):
        version = git.tag_containing(repo, str(pr["merge_commit_sha"]))
    verdict = status.derive(
        pr, pushed=pushed, conflict=conflict, checks=checks, review=review, version=version
    )
    slug = state["slug"]
    compare_url = (
        f"https://github.com/{slug}/compare/{base}...{branch}?expand=1"
        if slug and pr is None and pushed
        else None
    )
    updated = _iso_to_epoch(pr["updated_at"]) if pr else None
    return {
        "repo": repo,
        "repo_name": Path(repo).name,
        "github": slug,
        "branch": branch,
        "base": base,
        "session": session,
        "author": pr["author"] if pr else None,
        "pr": pr,
        "status": verdict["status"],
        "label": verdict["label"],
        "needs_you": verdict["needs_you"],
        "version": version,
        "checks": checks,
        "review": review,
        "conflict": conflict,
        "pushed": pushed,
        "unpushed_commits": unpushed,
        "ahead": ahead,
        "behind": behind,
        "size": size,
        "updated_at": updated or tip_time,
        "compare_url": compare_url,
    }


def _worktree_branch(row: dict[str, Any]) -> tuple[str, str] | None:
    """(repository root, branch) for a session that works in a git checkout."""
    path = row.get("worktree_path") or row.get("cwd")
    if not path or not Path(str(path)).is_dir() or not git.is_repo(str(path)):
        return None
    try:
        repo = git.repo_root(str(path))
        branch = git.current_branch(str(path)) or str(row.get("branch") or "")
    except git.ReviewGitError:
        return None
    return (repo, branch) if branch else None


def build(
    sessions: list[dict[str, Any]],
    *,
    owners: dict[tuple[str, str], str] | None = None,
    refresh: bool = False,
) -> dict[str, Any]:
    """Rows for `sessions`. `owners` ({branch: session key}, from
    branch_owners) adds the branches each session pushed beyond the one it
    is on; those are listed only when they have a pull request."""
    owners = owners or {}
    by_repo: dict[str, list[tuple[dict[str, Any], str]]] = {}
    for row in sessions:
        found = _worktree_branch(row)
        if found:
            by_repo.setdefault(found[0], []).append((row, found[1]))
    rows: list[dict[str, Any]] = []
    repos: list[dict[str, Any]] = []
    for repo, members in by_repo.items():
        current = {branch for _, branch in members}
        pushed = [
            (session_row, branch)
            for session_row, _ in members
            for (owner_repo, branch), key in owners.items()
            if owner_repo == repo and key == session_row["session_key"] and branch not in current
        ]
        state = _repo_state(repo, [b for _, b in members] + [b for _, b in pushed], refresh=refresh)
        seen: set[str] = set()
        for session_row, branch in [*members, *pushed]:
            if branch == state["default"] or branch in seen:
                continue
            seen.add(branch)
            on_branch = branch in current
            pr = _pull_for(state["pulls"], branch)
            if pr is None and not on_branch:
                continue
            session = {
                "key": str(session_row["session_key"]),
                "name": str(session_row.get("name") or session_row["session_key"]),
            }
            settled_key = (repo, pr["number"], pr["head_sha"]) if pr else None
            built = _settled.get(settled_key) if settled_key and not on_branch else None
            if built is None:
                built = _row(repo, state, branch=branch, pr=pr, session=session, local=on_branch)
                if settled_key and built and (built["status"] == "closed" or built["version"]):
                    _settled[settled_key] = built
            if built:
                rows.append({**built, "session": session})
        repos.append(
            {
                "path": repo,
                "name": Path(repo).name,
                "github": state["slug"],
                "default_branch": state["default"],
                "connected": bool(state["token"]),
                "error": state["error"] or state["fetch_error"],
                "fetched_at": int(state["at"]),
            }
        )
    rows.sort(key=lambda r: (not r["needs_you"], -(r["updated_at"] or 0)))
    return {"rows": rows, "repos": repos}


def folder_members(history: Any, folder: str) -> list[dict[str, Any]]:
    """Session rows in a folder and its subfolders. Runs on the server's loop
    thread: the SQLite connection must not be touched from `build`'s thread."""
    return [
        row
        for row in history.sessions()
        if not row.get("test") and within(str(row.get("grp") or ""), folder)
    ]
