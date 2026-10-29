"""Touch ID approval: the signature is the authority, the token is not.

Keys and signatures come from the system openssl, the same binary the server
verifies with; the Secure Enclave is replaced by a software P-256 key."""

import asyncio
import base64
import json
import subprocess
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import pytest

from duckterm.helpers import security
from duckterm.persistence.history import HistoryStore
from duckterm.review import approvals, lists
from duckterm.server import Server

NO_SIGNING = ["-c", "tag.gpgsign=false", "-c", "commit.gpgsign=false"]
pytestmark = pytest.mark.skipif(
    not Path(approvals.OPENSSL).exists(), reason="needs the system openssl"
)


def run(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *NO_SIGNING, "-C", str(repo), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


class Key:
    """A software stand-in for the Mac app's Secure Enclave key."""

    def __init__(self, directory: Path) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        self.private = directory / "key.pem"
        subprocess.run(
            [approvals.OPENSSL, "ecparam", "-name", "prime256v1", "-genkey", "-noout"]
            + ["-out", str(self.private)],
            check=True,
            capture_output=True,
        )
        der = subprocess.run(
            [approvals.OPENSSL, "ec", "-in", str(self.private), "-pubout", "-outform", "DER"],
            check=True,
            capture_output=True,
        ).stdout
        self.public = base64.b64encode(der).decode()
        self.directory = directory

    def bind(self) -> None:
        """What the Mac app does at launch: write the PEM into the home."""
        der = base64.b64decode(self.public)
        body = base64.b64encode(der).decode()
        lines = [body[i : i + 64] for i in range(0, len(body), 64)]
        path = approvals.key_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "-----BEGIN PUBLIC KEY-----\n" + "\n".join(lines) + "\n-----END PUBLIC KEY-----\n"
        )

    def sign(self, payload: str) -> str:
        message = self.directory / "message"
        message.write_bytes(payload.encode())
        signature = subprocess.run(
            [approvals.OPENSSL, "dgst", "-sha256", "-sign", str(self.private), str(message)],
            check=True,
            capture_output=True,
        ).stdout
        return base64.b64encode(signature).decode()


@pytest.fixture(autouse=True)
def isolated_key(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(approvals, "key_path", lambda: tmp_path / "bound" / "approval-key.pem")
    approvals._challenges.clear()


FIELDS = {
    "repo": "/r",
    "github": "o/r",
    "pr": "312",
    "head": "a" * 40,
    "folder": "Team/features",
    "release": "rel",
}


def test_the_key_file_must_hold_one_p256_key(tmp_path: Path) -> None:
    assert approvals.key_status()["enrolled"] is False
    approvals.key_path().parent.mkdir(parents=True, exist_ok=True)
    approvals.key_path().write_text(
        "-----BEGIN PUBLIC KEY-----\nnot a key\n-----END PUBLIC KEY-----\n"
    )
    status = approvals.key_status()
    assert status["enrolled"] is False and status["invalid"] is True
    key = Key(tmp_path / "owner")
    key.bind()
    status = approvals.key_status()
    assert status["enrolled"] is True and status["public_key"] == key.public
    assert len(status["fingerprint"].split()) == 4


def test_signature_must_come_from_the_bound_key_over_the_exact_payload(tmp_path: Path) -> None:
    owner, agent = Key(tmp_path / "owner"), Key(tmp_path / "agent")
    payload = approvals.challenge(FIELDS)
    assert approvals.verify(payload, owner.sign(payload)) is False  # nothing bound yet
    owner.bind()
    assert approvals.verify(payload, owner.sign(payload)) is True
    assert approvals.verify(payload, agent.sign(payload)) is False
    edited = payload.replace("head=" + "a" * 40, "head=" + "b" * 40)
    assert approvals.verify(edited, owner.sign(payload)) is False
    assert approvals.verify(payload, "not base64!") is False


def test_a_missing_verifier_refuses_and_says_so(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner = Key(tmp_path / "owner")
    owner.bind()
    payload = approvals.challenge(FIELDS)
    signature = owner.sign(payload)
    monkeypatch.setattr(approvals, "OPENSSL", str(tmp_path / "no-openssl"))
    with pytest.raises(approvals.ApprovalError) as missing:
        approvals.verify(payload, signature)
    assert missing.value.status == 503 and "missing" in str(missing.value)


def test_a_challenge_is_good_once_unedited_and_before_it_expires() -> None:
    payload = approvals.challenge(FIELDS, now=1000.0)
    assert approvals.parse_payload(payload)["head"] == "a" * 40
    edited = payload.replace("pr=312", "pr=313")
    with pytest.raises(approvals.ApprovalError):
        approvals.take_challenge(edited, now=1001.0)
    assert approvals.take_challenge(payload, now=1001.0)["pr"] == "312"
    with pytest.raises(approvals.ApprovalError) as replay:
        approvals.take_challenge(payload, now=1002.0)
    assert "already used" in str(replay.value)
    late = approvals.challenge(FIELDS, now=1000.0)
    with pytest.raises(approvals.ApprovalError) as expired:
        approvals.take_challenge(late, now=1000.0 + approvals.CHALLENGE_SECONDS + 1)
    assert "expired" in str(expired.value)
    with pytest.raises(approvals.ApprovalError):
        approvals.build_payload({**FIELDS, "nonce": "n", "expires": "1", "folder": "a\nb"})


def test_release_session_is_the_nearest_enclosing_release_dev_by_whole_segments() -> None:
    sessions = [
        {"session_key": "r-root", "name": "release-dev", "grp": "Team"},
        {"session_key": "r-other", "name": "release-dev", "grp": "Teamwork"},
        {"session_key": "r-gone", "name": "release-dev", "grp": "Team/features", "ended_at": 5},
        {"session_key": "dev", "name": "main-dev", "grp": "Team/features"},
    ]
    found = approvals.release_session(sessions, "Team/features")
    assert found is not None and found["session_key"] == "r-root"
    assert approvals.release_session(sessions, "Teams") is None
    assert approvals.release_session(sessions, "") is None


def test_preconditions_name_the_reason_for_each_failure() -> None:
    header: dict[str, Any] = {
        "head_sha": "a" * 40,
        "pr": {"number": 5, "state": "open", "draft": False, "head_sha": "a" * 40},
        "row": {"checks": {"total": 3, "failed": 0, "pending": 1, "passed": 2}, "conflict": True},
    }
    author = {"key": "dev", "name": "main-dev", "folder": "Team"}
    release = {"key": "dev", "name": "release-dev", "folder": "Team"}
    notes = {
        c["name"]: (c["ok"], c["note"]) for c in approvals.preconditions(header, author, release)
    }
    assert notes["Pull request open"] == (True, "#5")
    assert notes["CI green on this head"] == (False, "1 still running")
    assert notes["No conflict with base"] == (False, "conflict")
    assert notes["Release session"] == (False, "The release session cannot release its own branch")
    no_pr = approvals.preconditions({"head_sha": "a", "pr": None, "row": None}, None, None)
    assert all(not c["ok"] for c in no_pr)


# The end-to-end path over HTTP: real git, real server, real SQLite; GitHub stubbed.


@pytest.fixture
def fleet(git_repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    clone = tmp_path / "clone"
    subprocess.run(["git", "clone", "-q", str(git_repo), str(clone)], check=True)
    run(clone, "config", "user.email", "t@duckterm.local")
    run(clone, "config", "user.name", "T")
    run(clone, "checkout", "-q", "-b", "duckterm/feature")
    (clone / "feature.txt").write_text("one\n")
    run(clone, "add", "-A")
    run(clone, "commit", "-q", "-m", "feature")
    run(clone, "push", "-q", "-u", "origin", "duckterm/feature")
    state: dict[str, Any] = {"head": run(clone, "rev-parse", "HEAD")}

    def pulls(slug: str, token: str) -> list[dict[str, Any]]:
        return [
            {
                "number": 7,
                "title": "Feature",
                "body": "",
                "url": "https://github.com/o/r/pull/7",
                "state": "open",
                "draft": False,
                "author": "bot",
                "head": "duckterm/feature",
                "head_sha": state["head"],
                "base": "main",
                "merge_commit_sha": None,
                "merged_at": None,
                "updated_at": "2026-10-26T10:00:00Z",
                "created_at": "2026-10-26T09:00:00Z",
            }
        ]

    monkeypatch.setattr(lists.git, "github_slug", lambda repo: "o/r")
    monkeypatch.setattr(lists.github, "token", lambda: ("tok", None))
    monkeypatch.setattr(lists.github, "pulls", pulls)
    monkeypatch.setattr(
        lists.github, "check_runs", lambda *a: {"total": 2, "failed": 0, "pending": 0, "passed": 2}
    )
    monkeypatch.setattr(lists.github, "review_state", lambda *a: "none")
    lists._cache.clear()

    history = HistoryStore(tmp_path / "db.sqlite")
    for key, name, folder, worktree in (
        ("author-1", "main-dev", "Team/features", str(clone)),
        ("release-1", "release-dev", "Team", str(tmp_path)),
    ):
        history.record(
            {
                "_id": f"start-{key}",
                "_ts": 1,
                "event_type": "SessionStart",
                "session_key": key,
                "worktree_path": worktree,
                "test": True,
            }
        )
        history.set_meta(key, name=name, group=folder)
        history.session_api.ensure(key)
    history._conn.commit()
    return {"clone": clone, "history": history, "state": state, "tmp": tmp_path}


def call(port: int, method: str, path: str, body: object | None = None) -> tuple[int, Any]:
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}",
        data=None if body is None else json.dumps(body).encode(),
        headers={
            "Content-Type": "application/json",
            "X-Duckterm-Token": security.load_or_create_token(),
        },
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        text = error.read()
        try:
            return error.code, json.loads(text)
        except ValueError:
            return error.code, {"error": text.decode(errors="replace")}


def serve(history: HistoryStore, steps: Any) -> Any:
    async def scenario() -> Any:
        server = await asyncio.start_server(Server(history=history).handle, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        async with server:
            return await asyncio.to_thread(steps, port)

    return asyncio.run(scenario())


def test_touch_id_approval_hands_the_pinned_head_to_the_release_session(
    fleet: dict[str, Any],
) -> None:
    owner, agent = Key(fleet["tmp"] / "owner"), Key(fleet["tmp"] / "agent")
    clone, history, state = fleet["clone"], fleet["history"], fleet["state"]
    base = "/review/" + urllib.request.quote(str(clone), safe="") + "/7"

    def steps(port: int) -> dict[str, Any]:
        out: dict[str, Any] = {}
        # The review's demonstrated bypass: an agent with the install token
        # binds its own software key, then signs. Binding is not an HTTP route.
        out["agent_bind"] = call(port, "POST", "/approval-key", {"public_key": agent.public})
        out["unbound"] = call(port, "POST", base + "/approval/challenge", {})
        owner.bind()  # the Mac app, at launch
        out["key"] = call(port, "GET", "/approval-key")
        out["state"] = call(port, "GET", base + "/approval")
        status, issued = call(port, "POST", base + "/approval/challenge", {})
        assert status == 200, issued
        payload = issued["payload"]
        # An agent holding the install token cannot sign for the owner.
        out["forged"] = call(
            port,
            "POST",
            base + "/approval/submit",
            {"payload": payload, "signature": agent.sign(payload)},
        )
        status, issued = call(port, "POST", base + "/approval/challenge", {})
        payload = issued["payload"]
        out["approved"] = call(
            port,
            "POST",
            base + "/approval/submit",
            {"payload": payload, "signature": owner.sign(payload)},
        )
        out["replay"] = call(
            port,
            "POST",
            base + "/approval/submit",
            {"payload": payload, "signature": owner.sign(payload)},
        )
        out["list"] = call(port, "GET", "/sessions/author-1/prs?refresh=1")
        out["plain_github_approval"] = call(
            port, "POST", base + "/github-review", {"event": "APPROVE", "body": ""}
        )
        # The owner approves, then the branch moves before the approval lands.
        status, issued = call(port, "POST", base + "/approval/challenge", {})
        payload = issued["payload"]
        (clone / "feature.txt").write_text("two\n")
        run(clone, "commit", "-q", "-am", "moved after the click")
        run(clone, "push", "-q", "origin", "duckterm/feature")
        state["head"] = run(clone, "rev-parse", "HEAD")
        lists._cache.clear()
        out["moved"] = call(
            port,
            "POST",
            base + "/approval/submit",
            {"payload": payload, "signature": owner.sign(payload)},
        )
        return out

    approved_head = state["head"]
    out = serve(history, steps)

    assert out["agent_bind"][0] in (404, 405)
    assert out["unbound"][0] == 409 and "No Touch ID key" in out["unbound"][1]["error"]
    assert out["key"][1]["public_key"] == owner.public
    status, approval = out["state"]
    assert status == 200 and approval["ready"] is True, approval["checks"]
    assert approval["release"] == {"key": "release-1", "name": "release-dev", "folder": "Team"}
    assert approval["author"]["folder"] == "Team/features"

    assert out["forged"][0] == 403
    status, record = out["approved"]
    assert status == 200, record
    assert record["head_sha"] == approved_head
    assert record["task"]["status"] == "in_progress"
    assert record["task"]["note"] == "approved with Touch ID"
    assert out["replay"][0] == 409

    task = history.folder_tasks.get(record["task_id"])
    assert task["owner_session"] == "release-1" and task["folder"] == "Team"
    assert task["id"].startswith(approvals.TASK_PREFIX)
    assert task["title"] == f"Merge and release PR #7 at {approved_head[:12]}"
    message = history._conn.execute(
        "SELECT question FROM session_questions WHERE recipient = ?", ("release-1",)
    ).fetchall()
    assert len(message) == 1
    assert f"task update {record['task_id']}" in message[0]["question"]
    text = message[0]["question"]
    assert "This message is not the authority; the task row is." in text
    assert f"its id starts with {approvals.TASK_PREFIX}" in text
    assert "its owner_session is release-1, you" in text
    assert f"--squash --match-head-commit {approved_head}" in text
    # The signature proves presence at this Mac, not who; the text must not say otherwise.
    assert "owner's word" not in text and "owner's Touch ID" not in text

    rows = out["list"][1]["rows"]
    assert rows[0]["approval"]["task_id"] == record["task_id"]
    assert out["plain_github_approval"][0] == 400

    assert out["moved"][0] == 409 and "Head changed since approval" in out["moved"][1]["error"]
    tasks = history._conn.execute("SELECT count(*) FROM folder_tasks").fetchone()[0]
    assert tasks == 1
