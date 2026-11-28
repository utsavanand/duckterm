"""Read-only git facts for the PR review platform.

Every call runs against a session worktree or its main repository and never
changes a working tree, index or local branch. The only ref writes are the
remote-tracking refs and tags a bounded `fetch` brings in.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import threading
from pathlib import Path

from duckterm.helpers import paths
from duckterm.helpers.private_files import private_read, private_write


class ReviewGitError(RuntimeError):
    pass


# A repository's own config can name programs git will run: an fsmonitor,
# hooks, a signature verifier under log.showSignature, a diff textconv, and
# the command fetch runs to list an alternate object store's refs. Sessions
# report the folders read here and can write their .git/config, so none of
# them may run as the server.
_NO_REPO_PROGRAMS = (
    "-c",
    "core.fsmonitor=false",
    "-c",
    "core.hooksPath=/dev/null",
    "-c",
    "log.showSignature=false",
    "-c",
    "gpg.program=false",
    "-c",
    "gpg.ssh.program=false",
    "-c",
    "gpg.x509.program=false",
    "-c",
    "core.alternateRefsCommand=true",
)
# Transports git may use, given as GIT_ALLOW_PROTOCOL, which overrides every
# protocol.allow and protocol.<name>.allow setting. A transport can be a
# program (ext::, a remote helper), so a call into a session's repository
# allows none, or only a local folder (the mirror). The mirror reaches the
# real remote over https or ssh; tests add http and file.
_REMOTE_PROTOCOLS = ("https", "ssh")
# user@host:path, git's ssh shorthand. The host can't start with "-", and
# "name::" is a remote helper, not a host.
_SCP_URL = re.compile(r"^(?:[^@/:\s]+@)?[A-Za-z0-9][A-Za-z0-9.-]*:(?!:)")
# Fetching from the mirror needs no credentials, and none may be offered if
# the repository's url.<x>.insteadOf points the mirror's path elsewhere.
_LOCAL_FOLDER = [("credential.helper", ""), ("core.askPass", ""), ("core.sshCommand", "false")]
_OWNER_SCOPES = ("system", "global", "unknown")
_mirrors_lock = threading.Lock()
_mirror_locks: dict[Path, threading.Lock] = {}


# Programs a repository can attach to files through attributes, which it also
# controls (.git/info/attributes). diff-files runs a clean filter on racy index
# entries, and merge-tree runs a merge driver, so the repository's own are
# blanked on every call. A blank merge driver fails, which reports a conflict.
_REPO_DRIVERS = re.compile(r"^(filter\..*\.(clean|smudge|process)|merge\..*\.driver)$")


def _git(
    path: str | Path,
    args: list[str],
    timeout: float,
    pairs: list[tuple[str, str]],
    protocols: tuple[str, ...] = (),
) -> subprocess.CompletedProcess[str]:
    """Run git with the given config as GIT_CONFIG_* variables, so a key
    crafted to contain "=" can't be misparsed as it can with -c."""
    env = _env()
    env["GIT_CONFIG_COUNT"] = str(len(pairs))
    for i, (key, value) in enumerate(pairs):
        env[f"GIT_CONFIG_KEY_{i}"] = key
        env[f"GIT_CONFIG_VALUE_{i}"] = value
    env["GIT_ALLOW_PROTOCOL"] = ":".join(protocols)
    env["GIT_TERMINAL_PROMPT"] = "0"
    try:
        return subprocess.run(
            ["git", *_NO_REPO_PROGRAMS, "-C", str(path), *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
        )
    except subprocess.TimeoutExpired as exc:
        raise ReviewGitError(f"git {args[0]} timed out") from exc
    except OSError as exc:
        raise ReviewGitError(str(exc)) from exc


def _config_pairs(path: str | Path, timeout: float) -> list[tuple[str, str]]:
    """Blanks for the repository's own filters and merge drivers, read with
    one `git config` call (which runs nothing). The owner's are kept."""
    result = _git(
        path, ["config", "--show-scope", "-z", "--get-regexp", _REPO_DRIVERS.pattern], timeout, []
    )
    # exit 1: nothing matched
    fields = result.stdout.split("\0") if result.returncode == 0 else []
    pairs: list[tuple[str, str]] = []
    for scope, entry in zip(fields[::2], fields[1::2], strict=False):
        key = entry.partition("\n")[0]
        if scope not in _OWNER_SCOPES:
            pairs.append((key, ""))
            if key.startswith("filter."):
                pairs.append((key.rsplit(".", 1)[0] + ".required", "false"))
    return pairs


def _env() -> dict[str, str]:
    env = {
        k: v
        for k, v in os.environ.items()
        if not (k.startswith("GIT_") and k not in ("GIT_SSH", "GIT_SSH_COMMAND"))
    }
    env["GIT_OPTIONAL_LOCKS"] = "0"
    env["GIT_NO_LAZY_FETCH"] = "1"
    return env


def run(path: str | Path, *args: str, timeout: float = 15, local_folder: bool = False) -> str:
    """git in a repository a session may control: none of its programs run
    and nothing leaves this machine. local_folder lets fetch and ls-remote
    read another repository on disk."""
    pairs = _config_pairs(path, timeout) + (_LOCAL_FOLDER if local_folder else [])
    result = _git(path, list(args), timeout, pairs, ("file",) if local_folder else ())
    if result.returncode:
        raise ReviewGitError(result.stderr.strip() or f"git {args[0]} failed")
    return result.stdout


def is_repo(path: str | Path) -> bool:
    try:
        return run(path, "rev-parse", "--is-inside-work-tree").strip() == "true"
    except ReviewGitError:
        return False


def repo_root(path: str | Path) -> str:
    """The main repository of a worktree (or the repository itself)."""
    common = run(path, "rev-parse", "--path-format=absolute", "--git-common-dir").strip()
    return str(Path(common).resolve().parent)


def current_branch(path: str | Path) -> str | None:
    return run(path, "branch", "--show-current").strip() or None


def default_branch(repo: str | Path) -> str:
    try:
        ref = run(repo, "symbolic-ref", "--quiet", "--short", "refs/remotes/origin/HEAD").strip()
        if ref.startswith("origin/"):
            return ref[len("origin/") :]
    except ReviewGitError:
        pass
    for name in ("main", "master"):
        if ref_exists(repo, f"refs/heads/{name}") or ref_exists(
            repo, f"refs/remotes/origin/{name}"
        ):
            return name
    return "main"


def ref_exists(repo: str | Path, ref: str) -> bool:
    try:
        run(repo, "rev-parse", "--verify", "--quiet", "--end-of-options", ref)
        return True
    except ReviewGitError:
        return False


def resolve(repo: str | Path, ref: str, timeout: float = 15) -> str | None:
    try:
        return (
            run(
                repo,
                "rev-parse",
                "--verify",
                "--quiet",
                "--end-of-options",
                f"{ref}^{{commit}}",
                timeout=timeout,
            ).strip()
            or None
        )
    except ReviewGitError:
        return None


def base_ref(repo: str | Path, branch: str) -> str:
    """Prefer the remote-tracking ref for the base so a stale local `main`
    does not hide a conflict the remote already has."""
    remote = f"origin/{branch}"
    return remote if ref_exists(repo, f"refs/remotes/{remote}") else branch


_SLUG = re.compile(r"github\.com[:/]([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?/?$")


def github_slug(repo: str | Path) -> str | None:
    try:
        url = run(repo, "remote", "get-url", "origin").strip()
    except ReviewGitError:
        return None
    return slug_from_url(url)


def slug_from_url(url: str) -> str | None:
    match = _SLUG.search(url.strip())
    return f"{match[1]}/{match[2]}" if match else None


def ahead_behind(repo: str | Path, base: str, head: str) -> tuple[int, int]:
    out = run(
        repo, "rev-list", "--left-right", "--count", "--end-of-options", f"{base}...{head}"
    ).split()
    return int(out[1]), int(out[0])


def merge_base(repo: str | Path, base: str, head: str) -> str | None:
    try:
        return run(repo, "merge-base", "--end-of-options", base, head).strip() or None
    except ReviewGitError:
        return None


def conflicts(repo: str | Path, base: str, head: str) -> bool | None:
    """True when merging `head` into `base` would conflict, None when git
    cannot tell (missing ref, old git)."""
    if resolve(repo, base) is None or resolve(repo, head) is None:
        return None
    try:
        result = _git(
            repo,
            ["merge-tree", "--write-tree", "--name-only", "--end-of-options", base, head],
            30,
            _config_pairs(repo, 30),
        )
    except ReviewGitError:
        return None
    if result.returncode == 0:
        return False
    if result.returncode == 1:
        return True
    return None


def shortstat(repo: str | Path, base: str, head: str) -> dict[str, int] | None:
    try:
        out = run(
            repo,
            "diff",
            "--no-ext-diff",
            "--no-textconv",
            "--shortstat",
            "--end-of-options",
            f"{base}...{head}",
        )
    except ReviewGitError:
        return None
    files = additions = deletions = 0
    for part in out.strip().split(","):
        words = part.strip().split()
        if not words or not words[0].isdigit():
            continue
        value = int(words[0])
        if "file" in part:
            files = value
        elif "insertion" in part:
            additions = value
        elif "deletion" in part:
            deletions = value
    return {"files": files, "additions": additions, "deletions": deletions}


def commit_time(repo: str | Path, ref: str) -> int | None:
    try:
        return int(run(repo, "log", "-1", "--format=%ct", "--end-of-options", ref).strip())
    except (ReviewGitError, ValueError):
        return None


def tag_containing(repo: str | Path, sha: str) -> str | None:
    """The first release tag (creation order) that contains a commit."""
    try:
        out = run(repo, "tag", f"--contains={sha}", "--sort=creatordate", "--list", "v*")
    except ReviewGitError:
        return None
    tags = out.split()
    return tags[0] if tags else None


def _remote_url(url: str) -> bool:
    """A URL the mirror may fetch: an allowed scheme, or an absolute local
    path when tests allow file. Never one that starts like an option."""
    if url.startswith("-"):
        return False
    if "://" in url:
        scheme = url.split("://", 1)[0]
    elif _SCP_URL.match(url):
        scheme = "ssh"
    else:
        scheme = "file" if url.startswith("/") else ""
    return scheme in _REMOTE_PROTOCOLS


def _recorded_origin(root: str, seen: str) -> str:
    """The origin URL first seen for this clone. A session can change its
    origin later; the owner's credentials keep going to the first one."""
    record = paths.home() / "mirrors" / "repos.json"
    with _mirrors_lock:
        repos: dict[str, str] = json.loads(private_read(record) or "{}")
        if root not in repos and _remote_url(seen):
            repos[root] = seen
            private_write(record, json.dumps(repos, indent=2, sort_keys=True))
        return repos.get(root, "")


def _mirror(repo: str | Path) -> tuple[Path, str] | None:
    """A bare mirror DuckTerm owns, and the origin URL it fetches from.

    Only the mirror reaches the remote, with the owner's credentials, ssh
    command and URL rewrites. A session can write its own repository's config
    (a proxy, a rewrite, a helper, a new origin) but none of it is read on
    that call; the session's repository is then fetched from the mirror."""
    try:
        root = repo_root(repo)
        # The raw setting: `remote get-url` would apply the repository's
        # url.<x>.insteadOf.
        seen = run(repo, "config", "--get", "remote.origin.url").strip()
    except ReviewGitError:
        return None
    url = _recorded_origin(root, seen)
    if not _remote_url(url):
        return None
    tail = re.sub(r"[^A-Za-z0-9_.-]", "_", url.rstrip("/").rsplit("/", 1)[-1])[:40]
    name = f"{hashlib.sha256(url.encode()).hexdigest()[:16]}-{tail}"
    mirror = paths.home() / "mirrors" / f"{name}.git"
    if not (mirror / "HEAD").exists():
        mirror.parent.mkdir(parents=True, exist_ok=True)
        _owner_git(mirror.parent, ["init", "--quiet", "--bare", str(mirror)], 15)
    return mirror, url


def origin_unsupported(repo: str | Path) -> bool:
    """True when origin is set to a URL the mirror won't fetch, such as a
    local folder or file://, so the page can say why nothing is fetched."""
    try:
        root = repo_root(repo)
        seen = run(repo, "config", "--get", "remote.origin.url").strip()
    except ReviewGitError:
        return False
    return not _remote_url(_recorded_origin(root, seen))


def _owner_git(mirror: Path, args: list[str], timeout: float) -> str:
    pairs = [("gc.auto", "0"), ("maintenance.auto", "false")]
    with _mirrors_lock:
        lock = _mirror_locks.setdefault(mirror, threading.Lock())
    with lock:  # two fetches into one mirror would race for its ref locks
        result = _git(mirror, args, timeout, pairs, _REMOTE_PROTOCOLS)
    if result.returncode:
        raise ReviewGitError(result.stderr.strip() or f"git {args[0]} failed")
    return result.stdout


def remote_heads(repo: str | Path) -> dict[str, str] | None:
    """Branch -> tip sha on origin, from one `ls-remote` round trip. None when
    the remote is unreachable."""
    mirror = _mirror(repo)
    if mirror is None:
        return None
    try:
        out = _owner_git(mirror[0], ["ls-remote", "--heads", "--end-of-options", mirror[1]], 20)
    except ReviewGitError:
        return None
    heads: dict[str, str] = {}
    for line in out.splitlines():
        sha, _, ref = line.partition("\t")
        if ref.startswith("refs/heads/"):
            heads[ref[len("refs/heads/") :]] = sha
    return heads


def fetch(repo: str | Path, refs: list[str], *, tags: bool = True) -> str | None:
    """Bring the named remote branches ("*" for all) and the tags up to date,
    into the mirror and from there into the repository. Every named ref must
    exist on origin (see remote_heads). Bounded and non-fatal: returns the
    error text when the network is unavailable."""
    mirror = _mirror(repo)
    if mirror is None:
        return "no origin"
    tag_args = ["--tags"] if tags else []
    try:
        _owner_git(
            mirror[0],
            [
                "fetch",
                "--quiet",
                "--no-recurse-submodules",
                *tag_args,
                "--end-of-options",
                mirror[1],
            ]
            + [f"+refs/heads/{ref}:refs/heads/{ref}" for ref in refs],
            25,
        )
        run(
            repo,
            "fetch",
            "--quiet",
            "--no-recurse-submodules",
            *tag_args,
            "--end-of-options",
            str(mirror[0]),
            *[f"+refs/heads/{ref}:refs/remotes/origin/{ref}" for ref in refs],
            timeout=25,
            local_folder=True,
        )
    except ReviewGitError as exc:
        return str(exc) or "fetch failed"
    return None
