"""Repository selection and read-only cloning through the chosen GitHub connector."""

import base64
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

MAX_BUNDLE = 1024 * 1024 * 1024


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str
    ) -> None:
        return None


def repository_name(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", value):
        raise ValueError("Choose a GitHub repository in owner/name format")
    if any(part in (".", "..") for part in value.split("/")):
        raise ValueError("Invalid GitHub repository")
    return value


def local_token() -> tuple[str, dict[str, Any]]:
    from duckterm import connectors

    state = connectors.policy("github")
    if not state.get("enabled"):
        raise ValueError("Enable the GitHub connector on this computer to browse repositories")
    credential = connectors.github_token(str(state.get("source")))
    if not credential:
        raise ValueError("Reconnect the GitHub connector on this computer")
    return credential[0], state


def repositories(token: str, page: int) -> dict[str, Any]:
    if not 1 <= page <= 10000:
        raise ValueError("Invalid repository page")
    request = urllib.request.Request(
        f"https://api.github.com/user/repos?per_page=100&page={page}&sort=updated&affiliation=owner,collaborator,organization_member",
        headers={
            "Authorization": f"Bearer {token}",
            "User-Agent": "duckterm",
            "Accept": "application/vnd.github+json",
        },
    )
    try:
        with urllib.request.build_opener(NoRedirect).open(request, timeout=20) as response:
            body = response.read(4 * 1024 * 1024 + 1)
            if len(body) > 4 * 1024 * 1024:
                raise ValueError("GitHub repository response was too large")
            rows = json.loads(body)
            more = 'rel="next"' in response.headers.get("Link", "")
    except (OSError, urllib.error.URLError) as exc:
        raise ValueError(
            "Cannot list repositories; check GitHub connector access or rate limits"
        ) from exc
    if not isinstance(rows, list):
        raise ValueError("Invalid GitHub repository response")
    return {
        "repositories": [
            {
                "full_name": repository_name(row["full_name"]),
                "private": bool(row.get("private")),
                "default_branch": str(row.get("default_branch") or ""),
            }
            for row in rows
        ],
        "next_page": page + 1 if more else None,
    }


def make_bundle(token: str, repository: str, destination: Path) -> None:
    repository_name(repository)
    auth = base64.b64encode(f"x-access-token:{token}".encode()).decode()
    # Keep authentication out of argv, remote URLs and persistent Git config.
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "LANG": "C.UTF-8",
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_COUNT": "3",
        "GIT_CONFIG_KEY_0": "http.https://github.com/.extraHeader",
        "GIT_CONFIG_VALUE_0": f"Authorization: Basic {auth}",
        "GIT_CONFIG_KEY_1": "http.followRedirects",
        "GIT_CONFIG_VALUE_1": "false",
        "GIT_CONFIG_KEY_2": "credential.helper",
        "GIT_CONFIG_VALUE_2": "",
    }
    with tempfile.TemporaryDirectory(prefix="duckterm-github-") as temporary:
        mirror = Path(temporary) / "repo.git"
        commands = [
            [
                "git",
                "-c",
                "core.hooksPath=/dev/null",
                "-c",
                "init.templateDir=",
                "clone",
                "--mirror",
                "--",
                f"https://github.com/{repository}.git",
                str(mirror),
            ],
            ["git", "-C", str(mirror), "bundle", "create", str(destination), "--all"],
        ]
        for command in commands:
            try:
                result = subprocess.run(
                    command,
                    env=env,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=180,
                )
            except (OSError, subprocess.TimeoutExpired) as exc:
                raise ValueError("GitHub clone timed out or Git is unavailable") from exc
            if result.returncode:
                raise ValueError(
                    "GitHub clone failed; check connector repository access "
                    "and that the repository has commits"
                )
        if not 0 < destination.stat().st_size <= MAX_BUNDLE:
            raise ValueError("GitHub repository exceeds the 1 GiB clone limit")


def shared_request(
    operation: str, params: dict[str, Any], destination: Path | None = None
) -> dict[str, Any]:
    from duckterm import connector_client

    stream, ready = connector_client.connect("github-projects")
    with stream:
        if json.loads(ready).get("ready") is not True:
            raise ValueError(
                "GitHub repository browsing requires an enabled, updated connector host"
            )
        stream.settimeout(240)
        stream.sendall(json.dumps({"operation": operation, **params}).encode() + b"\n")
        with stream.makefile("rb") as reader:
            line = reader.readline(512 * 1024 + 1)
            if len(line) > 512 * 1024:
                raise ValueError("Invalid GitHub connector response")
            decoded = json.loads(line)
            if not isinstance(decoded, dict):
                raise ValueError("Invalid GitHub connector response")
            result: dict[str, Any] = decoded
            if result.get("error"):
                raise ValueError("GitHub connector request failed; check access and try again")
            if destination is not None:
                size = result.get("bytes")
                if not isinstance(size, int) or not 0 < size <= MAX_BUNDLE:
                    raise ValueError("Invalid GitHub repository size")
                digest = hashlib.sha256()
                with destination.open("xb") as output:
                    while size:
                        block = reader.read(min(size, 256 * 1024))
                        if not block:
                            raise ValueError("GitHub clone interrupted; retry")
                        output.write(block)
                        digest.update(block)
                        size -= len(block)
                if digest.hexdigest() != result.get("sha256"):
                    raise ValueError("GitHub clone verification failed")
            return result


def list_repositories(page: int) -> dict[str, Any]:
    from duckterm import connector_client

    if connector_client.configured():
        return shared_request("repositories", {"page": page})
    from duckterm import connectors

    token, state = local_token()
    result = repositories(token, page)
    identity = state.get("identity") or connectors.github_identity(token)
    if local_token() != (token, state):
        raise ValueError("GitHub connector changed; reload repositories")
    return {**result, "identity": identity}


def clone(repository: str, branch: str, destination: Path) -> None:
    from duckterm import connector_client

    repository_name(repository)
    with tempfile.TemporaryDirectory(prefix="duckterm-github-download-") as temporary:
        bundle = Path(temporary) / "repository.bundle"
        if connector_client.configured():
            shared_request("bundle", {"repository": repository}, bundle)
        else:
            token, state = local_token()
            make_bundle(token, repository, bundle)
            if local_token() != (token, state):
                raise ValueError("GitHub connector changed; retry the clone")
        args = ["git", "-c", "core.hooksPath=/dev/null", "clone"]
        if branch:
            if branch.startswith("-"):
                raise ValueError("Invalid branch")
            args += ["--branch", branch]
        try:
            result = subprocess.run(
                [*args, "--", str(bundle), str(destination)], capture_output=True, timeout=180
            )
            if result.returncode:
                raise ValueError("Could not check out GitHub repository; check the branch")
            subprocess.run(
                [
                    "git",
                    "-C",
                    str(destination),
                    "remote",
                    "set-url",
                    "origin",
                    f"https://github.com/{repository}.git",
                ],
                check=True,
                capture_output=True,
                timeout=10,
            )
        except subprocess.SubprocessError as exc:
            raise ValueError("GitHub checkout did not complete; retry the clone") from exc


def worker() -> None:
    """Broker-only protocol: metadata then bounded bundle bytes; no credentials leave."""
    try:
        request = json.loads(sys.stdin.buffer.readline(65537))
        token = os.environ["GITHUB_PERSONAL_ACCESS_TOKEN"]
        if request.get("operation") == "repositories":
            result = repositories(token, int(request.get("page", 1)))
            print(
                json.dumps({**result, "identity": os.environ.get("DUCKTERM_GITHUB_IDENTITY", "")}),
                flush=True,
            )
        elif request.get("operation") == "bundle":
            with tempfile.TemporaryDirectory(prefix="duckterm-github-export-") as temporary:
                bundle = Path(temporary) / "repository.bundle"
                make_bundle(token, request["repository"], bundle)
                with bundle.open("rb") as source:
                    digest = hashlib.file_digest(source, "sha256").hexdigest()
                    source.seek(0)
                    print(
                        json.dumps({"bytes": bundle.stat().st_size, "sha256": digest}), flush=True
                    )
                    while block := source.read(256 * 1024):
                        sys.stdout.buffer.write(block)
                    sys.stdout.buffer.flush()
        else:
            raise ValueError("Unsupported GitHub operation")
        # Keep the process alive until the client consumes the response. The
        # broker's revocation watcher remains active throughout the download.
        sys.stdin.buffer.read(1)
    except (ValueError, KeyError, OSError, subprocess.SubprocessError):
        print('{"error":"GitHub request failed"}', flush=True)


if __name__ == "__main__":
    worker()
