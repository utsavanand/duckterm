"""Reviewed project snapshots and resumable imports; no provider credentials travel.

Operation journals survive server/app restarts. A launch is claimed durably before
spawning: an ambiguous crash is reported for reconciliation, never retried blindly.
"""

import base64
import contextlib
import ctypes
import fcntl
import hashlib
import io
import json
import os
import re
import shlex
import shutil
import stat
import subprocess
import sys
import tarfile
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from duckterm.helpers import paths
from duckterm.helpers.private_files import private_write

CHUNK = 256 * 1024
MAX_BYTES = 1024 * 1024 * 1024
MAX_FILES = 50000
SKIP = {
    ".git",
    ".ssh",
    ".aws",
    ".gcloud",
    ".gnupg",
    ".kube",
    ".docker",
    ".npmrc",
    ".pypirc",
    ".netrc",
    "node_modules",
    ".venv",
    "venv",
    "__pycache__",
    ".DS_Store",
    ".duckterm-transfer-id",
    "dist",
    "build",
    ".next",
    ".cache",
}
SECRET = re.compile(
    r"(^\.env($|\.)|^(credentials|secrets?|tokens?|id_rsa|id_ed25519)(\..*)?$|\.(pem|key|p12|pfx)$)",
    re.I,
)
VERSIONS = {"claude-code": "2.1.267", "codex": "0.155.1"}


def git(root: Path, *args: str) -> bytes:
    proc = subprocess.run(
        [
            "git",
            "-c",
            "core.hooksPath=/dev/null",
            "-c",
            "core.fsmonitor=false",
            "-C",
            str(root),
            *args,
        ],
        capture_output=True,
        timeout=120,
    )
    if proc.returncode:
        raise ValueError("Git operation failed; check the repository and resolve conflicts first")
    return proc.stdout


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def operation_id(value: str) -> str:
    if not re.fullmatch(r"[a-f0-9]{32}", value):
        raise ValueError("Invalid transfer operation")
    return value


def safe_name(value: str) -> Path:
    p = Path(value)
    if (
        p.is_absolute()
        or not value
        or any(x in ("..", ".") for x in value.split("/"))
        or "\x00" in value
        or "\\" in value
    ):
        raise ValueError("Unsafe snapshot path")
    return p


def read_project_file(root: Path, relative: str) -> bytes:
    """Open each component without following symlinks, including during races."""
    parts = safe_name(relative).parts
    descriptor = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in parts[:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        file = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=descriptor)
        with os.fdopen(file, "rb") as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise ValueError("Project entry changed type during snapshot")
            data = stream.read(MAX_BYTES + 1)
            if len(data) > MAX_BYTES:
                raise ValueError("Project file exceeds transfer limit")
            return data
    finally:
        os.close(descriptor)


def private_root() -> Path:
    root = paths.home() / "transfers"
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    if root.is_symlink():
        raise ValueError("Transfer storage must not be a symlink")
    return root


@contextlib.contextmanager
def locked(identifier: str) -> Iterator[Path]:
    directory = private_root() / operation_id(identifier)
    directory.mkdir(mode=0o700, exist_ok=True)
    with (directory / "lock").open("a") as lock:
        os.chmod(directory / "lock", 0o600)
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield directory


def read_state(directory: Path) -> dict[str, Any]:
    try:
        return dict(json.loads((directory / "state.json").read_text()))
    except FileNotFoundError:
        return {}


def save(directory: Path, value: dict[str, Any]) -> dict[str, Any]:
    private_write(directory / "state.json", json.dumps(value))
    return value


def status(identifier: str) -> dict[str, Any]:
    with locked(identifier) as directory:
        return read_state(directory)


def check_url(url: str) -> None:
    if url.startswith("-") or any(c.isspace() for c in url):
        raise ValueError("Enter an HTTPS or SSH repository URL")
    parts = urlsplit(url)
    if (
        parts.scheme == "https"
        and parts.hostname
        and not parts.username
        and not parts.password
        and not parts.query
        and not parts.fragment
    ):
        return
    if (
        parts.scheme == "ssh"
        and parts.hostname
        and not parts.password
        and not parts.query
        and not parts.fragment
    ):
        return
    if re.fullmatch(r"[\w.-]+@[\w.-]+:[\w./~-]+", url):
        return
    raise ValueError(
        "Use HTTPS without embedded credentials, or SSH with the remote computer's login"
    )


def scan(root: Path, selected: list[str]) -> dict[str, Any]:
    root = root.expanduser().resolve(strict=True)
    protected = [
        Path.home() / name
        for name in (".ssh", ".codex", ".claude", ".aws", ".config/gcloud", ".gnupg")
    ]
    if (
        not root.is_dir()
        or root == Path.home()
        or root == Path("/")
        or any(root.is_relative_to(folder.resolve()) for folder in protected)
    ):
        raise ValueError("Choose a project folder, not a home or filesystem root")
    is_git = (root / ".git").exists()
    ignored: set[str] = set()
    tracked: set[str] = set()
    meta: dict[str, Any] = {"kind": "folder"}
    if is_git:
        if git(root, "rev-parse", "--show-toplevel").decode().strip() != str(root):
            raise ValueError("Choose the repository or worktree root")
        if git(root, "ls-files", "-u"):
            raise ValueError("Resolve Git conflicts before transferring")
        tracked = set(git(root, "ls-files", "-z").decode().rstrip("\0").split("\0")) - {""}
        index = git(root, "ls-files", "--stage").decode()
        if (
            any(line.startswith("160000 ") for line in index.splitlines())
            or (root / ".gitmodules").exists()
        ):
            raise ValueError("Submodules are not supported; use an existing remote checkout")
        ignored = set(
            git(root, "ls-files", "--others", "--ignored", "--exclude-standard", "-z")
            .decode()
            .rstrip("\0")
            .split("\0")
        ) - {""}
        remotes = {}
        for remote in git(root, "remote").decode().splitlines():
            url = git(root, "remote", "get-url", remote).decode().strip()
            check_url(url)
            remotes[remote] = url
        head = git(root, "rev-parse", "HEAD").decode().strip()
        branch = git(root, "rev-parse", "--abbrev-ref", "HEAD").decode().strip()
        meta = {
            "kind": "git",
            "head": head,
            "branch": branch,
            "remotes": remotes,
            "index": digest(git(root, "diff", "--cached", "--binary", "--no-ext-diff", "HEAD")),
        }
    selected_set = set(selected)
    if not selected_set.issubset(ignored):
        raise ValueError("Only listed ignored files can be selected")
    entries: list[dict[str, Any]] = []
    excluded: list[str] = []
    total = 0
    for folder, dirs, files in os.walk(root, followlinks=False):
        base = Path(folder)
        for name in list(dirs):
            if name in SKIP or name in (".claude", ".codex"):
                prefix = str((base / name).relative_to(root)) + "/"
                if any(p.startswith(prefix) for p in tracked):
                    raise ValueError(
                        f"Tracked files in excluded directory require review: {prefix}"
                    )
                dirs.remove(name)
                excluded.append(str((base / name).relative_to(root)) + "/")
            elif (base / name).is_symlink():
                dirs.remove(name)
                files.append(name)
            elif name != ".git" and (base / name / ".git").exists():
                raise ValueError("Nested repositories are unsupported; transfer them separately")
        for name in files:
            file = base / name
            rel = str(file.relative_to(root))
            if name in SKIP or SECRET.search(name) or (rel in ignored and rel not in selected_set):
                if rel in tracked and (name in SKIP or SECRET.search(name)):
                    raise ValueError(
                        f"Tracked credential/build file requires review before transfer: {rel}"
                    )
                excluded.append(rel)
                continue
            info = file.lstat()
            entry: dict[str, Any] = {"path": rel, "executable": bool(info.st_mode & 0o111)}
            if stat.S_ISLNK(info.st_mode):
                target = os.readlink(file)
                if Path(target).is_absolute() or not file.resolve().is_relative_to(root):
                    raise ValueError(f"Symlink escapes the project: {rel}")
                entry.update(link=target, size=0)
            elif stat.S_ISREG(info.st_mode):
                if info.st_size > MAX_BYTES:
                    raise ValueError("A file exceeds the 1 GiB transfer limit")
                data = read_project_file(root, rel)
                if data.startswith(b"version https://git-lfs.github.com/spec/v1"):
                    raise ValueError("Git LFS requires an existing remote checkout")
                if name == ".gitattributes" and b"filter=lfs" in data:
                    raise ValueError("Git LFS requires an existing remote checkout")
                if any(
                    mark in data[:65536]
                    for mark in (
                        b"-----BEGIN PRIVATE KEY-----",
                        b"-----BEGIN OPENSSH PRIVATE KEY-----",
                        b"-----BEGIN RSA PRIVATE KEY-----",
                    )
                ):
                    raise ValueError(f"Private key detected; exclude it before transfer: {rel}")
                total += len(data)
                entry.update(size=len(data), sha256=digest(data))
            else:
                excluded.append(rel)
                continue
            entries.append(entry)
            if total > MAX_BYTES or len(entries) > MAX_FILES:
                raise ValueError("Project exceeds the 1 GiB / 50,000 file transfer limit")
    entries.sort(key=lambda e: e["path"])
    requirements: list[str] = []
    setup: list[str] = []
    linux_compatible = True
    if (root / "package.json").is_file():
        package = json.loads((root / "package.json").read_text())
        operating_systems = package.get("os", [])
        if operating_systems and (
            "!linux" in operating_systems
            or (
                any(not x.startswith("!") for x in operating_systems)
                and "linux" not in operating_systems
            )
        ):
            linux_compatible = False
        requirements += ["node", "npm"]
        setup.append("Review package scripts and install Node dependencies on the destination.")
    if any((root / name).exists() for name in ("pyproject.toml", "requirements.txt", "setup.py")):
        requirements.append("python3")
        setup.append("Create a Linux virtual environment and install reviewed Python dependencies.")
    for manifest, binary in (("Cargo.toml", "cargo"), ("go.mod", "go"), ("Gemfile", "ruby")):
        if (root / manifest).is_file():
            requirements.append(binary)
            setup.append(f"Review and install {binary} project dependencies on the destination.")
    result = {
        "source": str(root),
        "requirements": requirements,
        "linux_compatible": linux_compatible,
        "setup": setup,
        "entries": entries,
        "excluded": sorted(excluded),
        "ignored": sorted(ignored),
        "bytes": total,
        "git": meta,
    }
    result["fingerprint"] = digest(json.dumps(result, sort_keys=True).encode())
    return result


def conversation(row: dict[str, Any], sid: str | None) -> dict[str, str]:
    runtime = row.get("runtime")
    if runtime not in VERSIONS:
        raise ValueError("Move supports Claude Code and Codex conversations only")
    if not sid:
        raise ValueError(
            "No exact provider conversation ID is recorded; cannot safely resume this session"
        )
    try:
        sid = str(uuid.UUID(sid))
    except ValueError as exc:
        raise ValueError("No valid exact provider conversation ID is recorded") from exc
    from duckterm.runtimes.claude_code import ClaudeCodeRuntime
    from duckterm.runtimes.codex import CodexRuntime

    provider = ClaudeCodeRuntime() if runtime == "claude-code" else CodexRuntime()
    transcript = provider.locate_transcript(cwd=Path(row["cwd"]), session_id=sid)
    if transcript is None or transcript.is_symlink():
        raise ValueError("The recorded conversation transcript is unavailable")
    check_runtime("claude" if runtime == "claude-code" else "codex", runtime)
    # Do not guess by newest transcript or directory. Verify the exact id in content.
    records = [json.loads(line) for line in transcript.read_text().splitlines() if line.strip()]
    ids = (
        {r.get("sessionId") for r in records}
        if runtime == "claude-code"
        else {r.get("payload", {}).get("id") for r in records if r.get("type") == "session_meta"}
    )
    if sid not in ids:
        raise ValueError("Transcript does not match the recorded conversation ID")
    return {
        "runtime": runtime,
        "id": sid,
        "transcript": str(transcript),
        "version": VERSIONS[runtime],
        "sha256": digest(transcript.read_bytes()),
    }


def check_runtime(command: str, runtime: str | None = None) -> str:
    argv = shlex.split(command)
    if not argv or not shutil.which(argv[0]):
        raise ValueError("The selected agent/runtime is not installed on the destination")
    if runtime:
        proc = subprocess.run([argv[0], "--version"], capture_output=True, text=True, timeout=10)
        if proc.returncode or not re.search(
            r"(?<![\d.])" + re.escape(VERSIONS[runtime]) + r"(?![\d.])", proc.stdout
        ):
            raise ValueError(
                f"Conversation transfer currently supports {runtime} {VERSIONS[runtime]} only"
            )
    return argv[0]


def check_requirements(binaries: list[str], linux_compatible: bool) -> None:
    if sys.platform.startswith("linux") and not linux_compatible:
        raise ValueError("This project declares that Linux is unsupported")
    allowed = {"node", "npm", "python3", "cargo", "go", "ruby"}
    if not set(binaries).issubset(allowed):
        raise ValueError("Invalid runtime requirements")
    missing = [name for name in binaries if shutil.which(name) is None]
    if missing:
        raise ValueError(
            "Install required runtimes on the destination before transfer: " + ", ".join(missing)
        )


def prepare(
    identifier: str,
    source: str,
    selected: list[str],
    fingerprint: str,
    conv: dict[str, str] | None = None,
    source_session: str | None = None,
) -> dict[str, Any]:
    with locked(identifier) as directory:
        previous = read_state(directory)
        if previous:
            if previous.get("fingerprint") != fingerprint:
                raise ValueError("Operation belongs to another snapshot")
            return previous
        snapshot = scan(Path(source), selected)
        if snapshot["fingerprint"] != fingerprint:
            raise ValueError(
                "Project changed after review; review the new snapshot before transferring"
            )
        root = Path(snapshot["source"])
        snapshot["conversation"] = {k: v for k, v in (conv or {}).items() if k != "transcript"}
        snapshot["source_session"] = source_session
        archive = directory / "snapshot.tar"
        try:
            with tarfile.open(archive, "w") as tar:

                def add(name: str, data: bytes, mode: int = 0o600) -> None:
                    info = tarfile.TarInfo(name)
                    info.size, info.mode = len(data), mode
                    tar.addfile(info, io.BytesIO(data))

                add("manifest.json", json.dumps(snapshot).encode())
                if snapshot["git"]["kind"] == "git":
                    git(root, "bundle", "create", str(directory / "history.bundle"), "HEAD")
                    add("history.bundle", (directory / "history.bundle").read_bytes())
                    add(
                        "index.patch",
                        git(root, "diff", "--cached", "--binary", "--no-ext-diff", "HEAD"),
                    )
                if conv:
                    data = Path(conv["transcript"]).read_bytes()
                    if digest(data) != conv["sha256"]:
                        raise ValueError("Conversation changed after review")
                    add("conversation.jsonl", data)
                for entry in snapshot["entries"]:
                    if "link" not in entry:
                        data = read_project_file(root, entry["path"])
                        if digest(data) != entry["sha256"]:
                            raise ValueError(
                                "Project changed while taking snapshot; stop writers and retry"
                            )
                        add(
                            "project/" + entry["path"],
                            data,
                            0o700 if entry["executable"] else 0o600,
                        )
            os.chmod(archive, 0o600)
            if scan(root, selected)["fingerprint"] != fingerprint:
                raise ValueError("Project changed while taking snapshot; review it again")
            if archive.stat().st_size > MAX_BYTES:
                raise ValueError("Project plus Git history exceeds 1 GiB")
            with archive.open("rb") as stream:
                sha = hashlib.file_digest(stream, "sha256").hexdigest()
            return save(
                directory,
                {
                    "id": identifier,
                    "stage": "prepared",
                    "fingerprint": fingerprint,
                    "source": str(root),
                    "source_session": source_session,
                    "bytes": archive.stat().st_size,
                    "sha256": sha,
                    "conversation": snapshot["conversation"],
                },
            )
        except BaseException:
            archive.unlink(missing_ok=True)
            raise
        finally:
            (directory / "history.bundle").unlink(missing_ok=True)


def chunk(identifier: str, offset: int) -> dict[str, Any]:
    with locked(identifier) as directory:
        if read_state(directory).get("stage") not in ("prepared", "moved") or offset < 0:
            raise ValueError("Snapshot is not prepared")
        with (directory / "snapshot.tar").open("rb") as stream:
            stream.seek(offset)
            return {"data": base64.b64encode(stream.read(CHUNK)).decode()}


def destination_path(value: str) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute() or path.name in ("", ".", ".."):
        raise ValueError("Choose a new absolute destination folder")
    parent = path.parent.resolve(strict=True)
    if not parent.is_dir():
        raise ValueError("Destination parent must be an existing folder")
    dest = parent / path.name
    if dest.exists() or dest.is_symlink():
        raise ValueError("Destination already exists; choose a new folder")
    return dest


def begin(identifier: str, destination: str, size: int, sha: str) -> dict[str, Any]:
    if not 0 < size <= MAX_BYTES or not re.fullmatch(r"[a-f0-9]{64}", sha):
        raise ValueError("Invalid transfer size or checksum")
    with locked(identifier) as directory:
        previous = read_state(directory)
        if previous:
            if previous.get("sha256") != sha or previous.get("destination") != str(
                Path(destination).expanduser().resolve()
            ):
                raise ValueError("Operation already belongs to another transfer")
            return previous
        dest = destination_path(destination)
        if shutil.disk_usage(dest.parent).free < size * 3 + 64 * 1024 * 1024:
            raise ValueError("Not enough destination disk space for staging and verification")
        private_write(directory / "incoming.tar", "")
        return save(
            directory,
            {
                "id": identifier,
                "stage": "receiving",
                "destination": str(dest),
                "bytes": size,
                "sha256": sha,
                "offset": 0,
            },
        )


def receive(identifier: str, offset: int, data: str) -> dict[str, Any]:
    decoded = base64.b64decode(data, validate=True)
    if len(decoded) > CHUNK or offset < 0:
        raise ValueError("Invalid transfer chunk")
    with locked(identifier) as directory:
        state = read_state(directory)
        if state.get("stage") != "receiving":
            return state
        path = directory / "incoming.tar"
        actual = path.stat().st_size
        if offset < actual:
            with path.open("rb") as f:
                f.seek(offset)
                if f.read(len(decoded)) != decoded:
                    raise ValueError("Retried transfer chunk does not match")
        elif offset == actual and actual + len(decoded) <= state["bytes"]:
            with path.open("ab") as f:
                f.write(decoded)
                f.flush()
                os.fsync(f.fileno())
        else:
            raise ValueError("Unexpected transfer offset; resume from recorded status")
        return save(directory, {**state, "offset": path.stat().st_size})


def publish(stage: Path, destination: Path) -> None:
    """Atomic rename that refuses an existing destination, including an empty one."""
    libc = ctypes.CDLL(None, use_errno=True)
    if sys.platform == "darwin":
        result = libc.renamex_np(os.fsencode(stage), os.fsencode(destination), 4)
    else:
        result = libc.renameat2(-100, os.fsencode(stage), -100, os.fsencode(destination), 1)
    if result:
        raise OSError(
            ctypes.get_errno(), "Could not publish project without overwriting destination"
        )


def finish(identifier: str) -> dict[str, Any]:
    with locked(identifier) as directory:
        state = read_state(directory)
        if state.get("stage") in ("ready", "launching", "launched"):
            return state
        if state.get("stage") not in ("receiving", "publishing"):
            raise ValueError("Transfer is not ready for verification")
        dest = Path(state["destination"])
        marker = ".duckterm-transfer-id"
        if (
            state["stage"] == "publishing"
            and (dest / marker).is_file()
            and (dest / marker).read_text() == identifier
        ):
            return save(directory, {**state, "stage": "ready"})
        archive = directory / "incoming.tar"
        with archive.open("rb") as f:
            if (
                archive.stat().st_size != state["bytes"]
                or hashlib.file_digest(f, "sha256").hexdigest() != state["sha256"]
            ):
                raise ValueError("Snapshot is incomplete or checksum does not match")
        stage = dest.parent / (".duckterm-transfer-" + identifier)
        if stage.exists():
            if not (stage / marker).is_file() or (stage / marker).read_text() != identifier:
                raise ValueError("Staging folder is occupied")
            shutil.rmtree(stage)
        stage.mkdir(mode=0o700)
        private_write(stage / marker, identifier)
        try:
            with tarfile.open(archive) as tar:
                members = tar.getmembers()
                names = [m.name for m in members]
                if (
                    len(names) != len(set(names))
                    or len(names) > MAX_FILES + 4
                    or any(not m.isfile() for m in members)
                ):
                    raise ValueError("Snapshot contains unsafe or duplicate entries")
                if sum(m.size for m in members) > MAX_BYTES:
                    raise ValueError("Expanded snapshot exceeds transfer limit")

                def contents(name: str) -> bytes:
                    stream = tar.extractfile(name)
                    if stream is None:
                        raise ValueError("Missing snapshot entry")
                    return stream.read()

                snapshot = json.loads(contents("manifest.json"))
                entries = snapshot["entries"]
                check_requirements(
                    snapshot.get("requirements", []), snapshot.get("linux_compatible", True)
                )
                allowed = {
                    "manifest.json",
                    "history.bundle",
                    "index.patch",
                    "conversation.jsonl",
                } | {"project/" + e["path"] for e in entries if "link" not in e}
                if not set(names).issubset(allowed):
                    raise ValueError("Unexpected snapshot files")
                if snapshot["git"]["kind"] == "git":
                    bundle = directory / "history.bundle"
                    bundle.write_bytes(contents("history.bundle"))
                    git(stage, "init", "--quiet")
                    git(stage, "fetch", "--quiet", str(bundle), "HEAD")
                    git(stage, "reset", "--hard", "FETCH_HEAD")
                    branch = snapshot["git"]["branch"]
                    if branch == "HEAD":
                        git(stage, "checkout", "--detach")
                    else:
                        git(stage, "check-ref-format", "--branch", branch)
                        git(stage, "checkout", "-B", branch)
                    if git(stage, "rev-parse", "HEAD").decode().strip() != snapshot["git"]["head"]:
                        raise ValueError("Git HEAD does not match snapshot")
                    patch = contents("index.patch")
                    if digest(patch) != snapshot["git"]["index"]:
                        raise ValueError("Git index checksum mismatch")
                    if patch:
                        (directory / "index.patch").write_bytes(patch)
                        git(stage, "apply", "--cached", str(directory / "index.patch"))
                    if (
                        digest(git(stage, "diff", "--cached", "--binary", "--no-ext-diff", "HEAD"))
                        != snapshot["git"]["index"]
                    ):
                        raise ValueError("Git index differs after reconstruction")
                    for remote, url in snapshot["git"].get("remotes", {}).items():
                        if not re.fullmatch(r"[A-Za-z0-9._-]+", remote) or remote.startswith("-"):
                            raise ValueError("Invalid Git remote name")
                        check_url(url)
                        git(stage, "remote", "add", remote, url)
                    # Replace checkout files with the reviewed working tree (including deletions).
                    for item in stage.iterdir():
                        if item.name not in (".git", marker):
                            if item.is_dir() and not item.is_symlink():
                                shutil.rmtree(item)
                            else:
                                item.unlink()
                seen: set[str] = set()
                for entry in entries:
                    rel = safe_name(entry["path"])
                    if rel.parts[0] == ".git" or rel.name == marker or str(rel) in seen:
                        raise ValueError("Unsafe project entry")
                    seen.add(str(rel))
                    target = stage / rel
                    if not target.parent.resolve().is_relative_to(stage):
                        raise ValueError("Snapshot path escapes staging")
                    target.parent.mkdir(parents=True, exist_ok=True)
                    if target.exists() or target.is_symlink():
                        raise ValueError("Snapshot paths overlap")
                    if "link" in entry:
                        link = Path(entry["link"])
                        if link.is_absolute() or not (
                            target.parent / link
                        ).resolve().is_relative_to(stage):
                            raise ValueError("Unsafe snapshot symlink")
                        target.symlink_to(link)
                    else:
                        data = contents("project/" + entry["path"])
                        if len(data) != entry["size"] or digest(data) != entry["sha256"]:
                            raise ValueError("Project file checksum mismatch")
                        target.write_bytes(data)
                        target.chmod(0o700 if entry["executable"] else 0o600)
                conv = snapshot.get("conversation") or {}
                if conv:
                    data = contents("conversation.jsonl")
                    if (
                        digest(data) != conv["sha256"]
                        or conv.get("runtime") not in VERSIONS
                        or conv.get("version") != VERSIONS[conv["runtime"]]
                    ):
                        raise ValueError("Unsupported or corrupt conversation")
                    uuid.UUID(conv["id"])
                    (directory / "conversation.jsonl").write_bytes(data)
                    (directory / "conversation.jsonl").chmod(0o600)
                state = save(
                    directory,
                    {
                        **state,
                        "stage": "publishing",
                        "conversation": conv,
                        "source_session": snapshot.get("source_session"),
                    },
                )
            publish(stage, dest)
            return save(directory, {**state, "stage": "ready"})
        except BaseException:
            if (
                stage.is_dir()
                and (stage / marker).is_file()
                and (stage / marker).read_text() == identifier
            ):
                shutil.rmtree(stage)
            raise


def clone(identifier: str, url: str, branch: str, destination: str) -> dict[str, Any]:
    check_url(url)
    destination = str(Path(destination).expanduser().resolve())
    with locked(identifier) as directory:
        previous = read_state(directory)
        if previous:
            if (
                previous.get("url") != url
                or previous.get("destination") != str(Path(destination).expanduser().resolve())
                or previous.get("branch") != branch
            ):
                raise ValueError("Operation belongs to another repository")
            if previous["stage"] in ("ready", "launching", "launched"):
                return previous
            marker = Path(destination) / ".duckterm-transfer-id"
            if (
                previous["stage"] == "publishing"
                and marker.is_file()
                and marker.read_text() == identifier
            ):
                return save(directory, {**previous, "stage": "ready"})
        dest = destination_path(destination)
        if shutil.disk_usage(dest.parent).free < 512 * 1024 * 1024:
            raise ValueError("Not enough destination disk space to clone safely")
        stage = directory / "clone"
        if stage.exists():
            shutil.rmtree(stage)
        state = save(
            directory,
            {
                "id": identifier,
                "stage": "cloning",
                "url": url,
                "branch": branch,
                "destination": destination,
            },
        )
        args = ["git", "-c", "core.hooksPath=/dev/null", "clone", "--no-local"]
        if branch:
            if branch.startswith("-"):
                raise ValueError("Invalid branch")
            args += ["--branch", branch]
        env = {
            **os.environ,
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_SSH_COMMAND": "ssh -oBatchMode=yes -oStrictHostKeyChecking=yes",
        }
        proc = subprocess.run(
            [*args, "--", url, str(stage)], env=env, capture_output=True, timeout=180
        )
        if proc.returncode:
            raise ValueError(
                "Clone failed; check repository URL and the remote computer's Git authorization"
            )
        # Apply the same unsupported-project checks; no repository hooks are run.
        compatibility = scan(stage, [])
        check_requirements(compatibility["requirements"], compatibility["linux_compatible"])
        sibling = dest.parent / (".duckterm-transfer-" + identifier)
        if sibling.exists():
            marker = sibling / ".duckterm-transfer-id"
            if not marker.is_file() or marker.read_text() != identifier:
                raise ValueError("Staging destination exists; choose another destination")
            shutil.rmtree(sibling)
        sibling.mkdir(mode=0o700)
        private_write(sibling / ".duckterm-transfer-id", identifier)
        shutil.copytree(stage, sibling, dirs_exist_ok=True)
        save(directory, {**state, "stage": "publishing"})
        publish(sibling, dest)
        return save(directory, {**state, "stage": "ready"})


def claim_launch(identifier: str, command: str, name: str, prompt: str) -> dict[str, Any]:
    with locked(identifier) as directory:
        state = read_state(directory)
        if state.get("stage") in ("launching", "launched"):
            return state
        if state.get("stage") != "ready":
            raise ValueError("Project must be verified before launching")
        conv = state.get("conversation") or {}
        if conv:
            runtime, sid = conv["runtime"], conv["id"]
            executable = "claude" if runtime == "claude-code" else "codex"
            check_runtime(executable, runtime)
            if runtime == "claude-code":
                command = shlex.join(
                    ["claude", "--resume", str(directory / "conversation.jsonl"), "--fork-session"]
                )
            else:
                target = (
                    Path.home()
                    / ".codex"
                    / "sessions"
                    / "imported"
                    / f"rollout-transfer-{sid}.jsonl"
                )
                data = (directory / "conversation.jsonl").read_text()
                if target.exists() and target.read_text() != data:
                    raise ValueError(
                        "A different copy of this conversation already exists remotely"
                    )
                private_write(target, data)
                command = shlex.join(["codex", "resume", sid])
        else:
            check_runtime(command)
        return save(
            directory,
            {
                **state,
                "stage": "launching",
                "command": command,
                "name": name,
                "prompt": prompt,
                "session_key": "transfer-" + identifier,
            },
        )


def mark_launched(identifier: str) -> dict[str, Any]:
    with locked(identifier) as directory:
        state = read_state(directory)
        return save(directory, {**state, "stage": "launched"})


def mark_moved(identifier: str, target: str, key: str) -> dict[str, Any]:
    with locked(identifier) as directory:
        state = read_state(directory)
        if state.get("stage") not in ("prepared", "moved"):
            raise ValueError("Source snapshot is unavailable")
        return save(directory, {**state, "stage": "moved", "target": target, "session_key": key})


def session_transfer(key: str) -> dict[str, Any] | None:
    for file in private_root().glob("*/state.json"):
        state = json.loads(file.read_text())
        if state.get("source_session") == key and state.get("stage") in ("prepared", "moved"):
            return dict(state)
    return None
