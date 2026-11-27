"""Read-only git facts for the PR review platform.

Every call runs against a session worktree or its main repository and never
changes a working tree, index or local branch. The only ref writes are the
remote-tracking refs and tags a bounded `fetch` brings in.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path


class ReviewGitError(RuntimeError):
    pass


# A repository's own config can name programs git will run: an fsmonitor,
# hooks, a signature verifier under log.showSignature, a diff textconv, the
# command fetch runs to list an alternate object store's refs, and an ssh
# command, credential helper or remote helper when git reaches a remote.
# Sessions report the folders read here and can write their .git/config, so
# none of them may run as the server. Only fetch and remote_heads are meant to
# reach a remote; see _config_pairs for what they keep.
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
    # ext:: runs a command as the transport and fd:: talks to an open file
    # descriptor. A repository's protocol.<name>.allow beats protocol.allow,
    # so these two are pinned on every call.
    "-c",
    "protocol.ext.allow=never",
    "-c",
    "protocol.fd.allow=never",
)
_OFFLINE = ("-c", "protocol.allow=never")
# file covers a clone whose origin is a local folder. It runs upload-pack on
# this machine, so fetch and ls-remote pass --upload-pack: git keeps the first
# remote.origin.uploadpack it reads, so the repo's own beats any -c override.
_REMOTE_PROTOCOLS = ("https", "ssh", "file")
_OWNER_SCOPES = ("system", "global", "unknown")
# Settings that decide where a request goes or which certificate it trusts.
# Set by the repository, they could send the owner's credentials to another
# host, and a URL-specific http.<url>.* key there beats any general override,
# so a call whose repository sets one is refused rather than overridden.
# protocol.<name>.allow and remote.<name>.vcs choose the transport, which for
# a remote helper is a program. Push URLs are left alone: fetch never reads
# them and DuckTerm never pushes.
_REPO_NETWORK_KEYS = re.compile(
    r"^(https?\..*|remote\..*\.proxy|url\..*\.insteadof|core\.gitproxy|core\.askpass"
    r"|protocol\..*|remote\..*\.vcs)$"
)


# Programs a repository can attach to files through attributes, which it also
# controls (.git/info/attributes). diff-files runs a clean filter on racy index
# entries, and merge-tree runs a merge driver, so the repository's own are
# blanked on every call. A blank merge driver fails, which reports a conflict.
_REPO_DRIVERS = re.compile(r"^(filter\..*\.(clean|smudge|process)|merge\..*\.driver)$")


def _git(
    path: str | Path, args: list[str], timeout: float, pairs: list[tuple[str, str]], network: bool
) -> subprocess.CompletedProcess[str]:
    """Run git with the given config as GIT_CONFIG_* variables, so a key
    crafted to contain "=" can't be misparsed as it can with -c."""
    env = _env()
    env["GIT_CONFIG_COUNT"] = str(len(pairs))
    for i, (key, value) in enumerate(pairs):
        env[f"GIT_CONFIG_KEY_{i}"] = key
        env[f"GIT_CONFIG_VALUE_{i}"] = value
    if network:
        env["GIT_TERMINAL_PROMPT"] = "0"
    try:
        return subprocess.run(
            ["git", *_NO_REPO_PROGRAMS, *(() if network else _OFFLINE), "-C", str(path), *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
        )
    except subprocess.TimeoutExpired as exc:
        raise ReviewGitError(f"git {args[0]} timed out") from exc
    except OSError as exc:
        raise ReviewGitError(str(exc)) from exc


def _config_pairs(path: str | Path, timeout: float, network: bool) -> list[tuple[str, str]]:
    """Config overriding what the repository may not choose, read with one
    `git config` call (which runs nothing). Every call blanks the repository's
    filters and merge drivers. A call that reaches a remote also keeps the
    owner's own credential helpers and ssh command (system, global or the git
    install's own file) and drops the repository's: an empty credential.helper
    clears every helper read so far, and the owner's are given again after it."""
    pattern = _REPO_DRIVERS.pattern
    if network:
        pattern += r"|^credential\..*helper$|^core\.sshcommand$|" + _REPO_NETWORK_KEYS.pattern
    result = _git(
        path, ["config", "--show-scope", "-z", "--get-regexp", pattern], timeout, [], False
    )
    # exit 1: nothing matched
    fields = result.stdout.split("\0") if result.returncode == 0 else []
    pairs: list[tuple[str, str]] = []
    if network:
        pairs += [
            ("protocol.allow", "never"),
            *((f"protocol.{name}.allow", "always") for name in _REMOTE_PROTOCOLS),
            ("core.askPass", ""),
            ("gc.auto", "0"),
            ("maintenance.auto", "false"),
            ("credential.helper", ""),
        ]
    ssh = "ssh"
    for scope, entry in zip(fields[::2], fields[1::2], strict=False):
        key, _, value = entry.partition("\n")
        if scope == "command":  # DuckTerm's own -c settings
            continue
        owner = scope in _OWNER_SCOPES
        if _REPO_DRIVERS.match(key):
            if not owner:
                pairs.append((key, ""))
                if key.startswith("filter."):
                    pairs.append((key.rsplit(".", 1)[0] + ".required", "false"))
        elif _REPO_NETWORK_KEYS.match(key):
            if not owner:
                raise ReviewGitError(f"remote unreachable: the repository sets {key}")
        elif not owner:
            continue
        elif key == "core.sshcommand":
            ssh = value
        else:
            pairs.append((key, value))
    if network:
        pairs.append(("core.sshCommand", ssh))
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


def run(path: str | Path, *args: str, timeout: float = 15, network: bool = False) -> str:
    result = _git(path, list(args), timeout, _config_pairs(path, timeout, network), network)
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
            _config_pairs(repo, 30, False),
            False,
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


def remote_heads(repo: str | Path) -> dict[str, str] | None:
    """Branch -> tip sha on origin, from one `ls-remote` round trip. None when
    the remote is unreachable."""
    try:
        out = run(
            repo,
            "ls-remote",
            "--upload-pack=git-upload-pack",
            "--heads",
            "origin",
            timeout=20,
            network=True,
        )
    except ReviewGitError:
        return None
    heads: dict[str, str] = {}
    for line in out.splitlines():
        sha, _, ref = line.partition("\t")
        if ref.startswith("refs/heads/"):
            heads[ref[len("refs/heads/") :]] = sha
    return heads


def fetch(repo: str | Path, refs: list[str], *, tags: bool = True) -> str | None:
    """Bring the named remote branches and the tags up to date. Every ref must
    exist on origin (see remote_heads). Bounded and non-fatal: returns the
    error text when the network is unavailable."""
    args = ["fetch", "--quiet", "--no-recurse-submodules", "--upload-pack=git-upload-pack"]
    if tags:
        args.append("--tags")
    args.append("origin")
    args += [f"+refs/heads/{ref}:refs/remotes/origin/{ref}" for ref in refs]
    try:
        run(repo, *args, timeout=25, network=True)
    except ReviewGitError as exc:
        return str(exc) or "fetch failed"
    return None
