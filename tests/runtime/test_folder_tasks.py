"""Only explicit reporting/assignment creates tasks; no governance is attached."""

import sqlite3

import pytest
from tests.runtime.test_session_api import call, enroll

from duckterm.core.folder_messages import FolderMessages
from duckterm.core.session_api import APIError
from duckterm.persistence.history import HistoryStore


@pytest.fixture
def rig(tmp_path, monkeypatch):
    monkeypatch.setenv("DUCKTERM_HOME", str(tmp_path))
    h = HistoryStore(tmp_path / "tasks.sqlite")
    for key, folder in (("a", "work"), ("b", "work"), ("c", "other")):
        h.record(
            {"_id": key, "_ts": 1, "session_key": key, "event_type": "SessionStart", "test": True}
        )
        h.set_meta(key, name=key, group=folder)
    creds = {
        key: enroll(h, key, root) for key, root in (("a", "work"), ("b", "work"), ("c", "other"))
    }
    yield h, creds
    h.close()


def task(h, creds, method="POST", path="/tasks", body=None):
    return call(h, creds, method, path, body)[1]


def assignment(h, assign=True, key="one"):
    service = FolderMessages(h)
    recipients = service.recipients("work")
    return service.send(
        "work",
        {
            "identity": recipients["identity"],
            "target": {"kind": "session", "id": "b"},
            "recipients": ["b"],
            "text": "  Review layout\n",
            "request_key": key,
            "assign": assign,
        },
    )


def test_explicit_start_handoff_and_optional_completion(rig):
    h, creds = rig
    row = task(h, creds["a"], body={"title": "Review layout"})["task"]
    identity = row["id"]
    assert row["status"] == "in_progress"
    handed = task(h, creds["a"], path=f"/tasks/{identity}/handoff", body={"session": "b"})["task"]
    assert (
        handed["id"] == identity and handed["owner_session"] == "b" and handed["folder"] == "work"
    )
    for status in ("parked", "done", "in_progress"):
        result = task(h, creds["b"], "PATCH", f"/tasks/{identity}", {"status": status})["task"]
        assert result["status"] == status and result["note"] == ""
    task(h, creds["b"], "PATCH", "/self", {"activity": "Checking contrast"})
    assert task(h, creds["a"], "GET")["tasks"][0]["activity"] == "Checking contrast"
    assert h._conn.execute("SELECT count(*) FROM session_questions").fetchone()[0] == 0


def test_scopes_and_ordinary_mail_never_create_tasks(rig):
    h, creds = rig
    assignment(h, False)
    assert h.folder_tasks.list_tasks() == []
    row = task(h, creds["a"], body={"title": "One task"})["task"]
    for method, path, body in (
        ("GET", "/tasks?folder=work", {}),
        ("PATCH", f"/tasks/{row['id']}", {"status": "done"}),
    ):
        with pytest.raises(APIError) as exc:
            task(h, creds["c"], method, path, body)
        assert exc.value.status == 403
    question = call(
        h,
        {**creds["a"], "idempotency-key": "peer"},
        "POST",
        "/questions",
        {"target_session_id": "b", "question": "@b do this"},
    )[1]
    call(h, creds["b"], "POST", f"/questions/{question['id']}/accept")
    assert len(h.folder_tasks.list_tasks()) == 1


def test_assignment_and_mail_share_one_transaction_and_retry(rig, monkeypatch):
    h, _ = rig
    first = assignment(h)
    assert assignment(h) == first
    rows = h.folder_tasks.list_tasks()
    assert len(rows) == 1 and rows[0]["title"] == "  Review layout\n"
    assert rows[0]["id"] == first["dispatch"]["recipients"][0]["message_id"]
    with pytest.raises(APIError):
        assignment(h, False)

    def fail(*args, **kwargs):
        raise OSError("task write failed")

    monkeypatch.setattr(h.folder_tasks, "start", fail)
    with pytest.raises(OSError):
        assignment(h, key="two")
    assert (
        h._conn.execute("SELECT count(*) FROM session_questions WHERE sender='owner'").fetchone()[0]
        == 1
    )
    assert len(h.folder_tasks.list_tasks()) == 1


def test_retry_after_chat_save_failure_keeps_one_assignment(rig, monkeypatch):
    h, _ = rig
    append = h.folder_chats.append

    def fail(*args, **kwargs):
        raise OSError("chat persistence failed")

    monkeypatch.setattr(h.folder_chats, "append", fail)
    with pytest.raises(OSError):
        assignment(h)
    monkeypatch.setattr(h.folder_chats, "append", append)
    assignment(h)
    assert len(h.folder_tasks.list_tasks()) == 1
    assert (
        h._conn.execute("SELECT count(*) FROM session_questions WHERE sender='owner'").fetchone()[0]
        == 1
    )


def test_archive_failure_prevents_drop(tmp_path, monkeypatch):
    from duckterm.core.folder_tasks import migrate

    conn = sqlite3.connect(tmp_path / "legacy.sqlite")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE session_work(id TEXT)")
    conn.execute("INSERT INTO session_work VALUES ('legacy')")
    conn.commit()

    def fail(*args):
        raise OSError("disk full")

    monkeypatch.setattr("duckterm.core.folder_tasks.private_write", fail)
    with pytest.raises(OSError):
        migrate(conn, tmp_path)
    assert conn.execute("SELECT id FROM session_work").fetchone()[0] == "legacy"
    conn.close()


def test_oldest_active_first_and_folder_rename_preserves_identity(rig):
    h, creds = rig
    one = task(h, creds["a"], body={"title": "Old"})["task"]
    two = task(h, creds["b"], body={"title": "New"})["task"]
    h._conn.execute("UPDATE folder_tasks SET created_at=1 WHERE id=?", (one["id"],))
    h._conn.commit()
    assert [row["id"] for row in h.folder_tasks.list_tasks()] == [one["id"], two["id"]]
    task(h, creds["a"], "PATCH", f"/tasks/{one['id']}", {"status": "done"})
    assert h.folder_tasks.list_tasks()[0]["id"] == two["id"]
    h.move_folder("work", "renamed")
    assert h.folder_tasks.get(one["id"])["folder"] == "renamed"


def test_scoped_task_read_does_not_disclose_relocated_owner_activity(rig):
    h, creds = rig
    task(h, creds["b"], body={"title": "Public work"})
    h.set_meta("b", group="other", name="private name")
    h._conn.execute(
        "UPDATE session_api_members SET activity='private activity' WHERE session_key='b'"
    )
    h._conn.commit()
    row = task(h, creds["a"], "GET")["tasks"][0]
    assert row["owner_name"] is None and row["activity"] is None
