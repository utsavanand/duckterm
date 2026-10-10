"""A report draft contains only reviewed bytes and is never sent by the server."""

import asyncio
import base64
import json
import os
import zipfile
from email import policy
from email.parser import BytesParser
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest
from tests.runtime.test_session_api import dispatch

from duckterm import bug_reports
from duckterm.persistence.history import HistoryStore
from duckterm.server import Server
from duckterm.transport.httpio import read_body


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("DUCKTERM_SUPPORT_EMAIL", "bugs@example.test")
    history = HistoryStore(tmp_path / "db.sqlite")
    for key in ("selected", "other"):
        history.record(
            {
                "_id": key,
                "_ts": 12,
                "event_type": "SessionStart",
                "session_key": key,
                "runtime": "codex",
                "test": True,
                "cwd": "/SECRET/path",
                "prompt": "SECRET prompt",
                "tool_input": {"secret": "SECRET token"},
            }
        )
    history.set_state("other", "waiting")
    history.set_model("selected", "example-model")
    server = Server(history=history)
    yield server
    history.purge_test_sessions()
    history.close()


def request(app, method, path, data=None, headers=None):
    return dispatch(
        app,
        method,
        path,
        {"x-duckterm-token": app.token} if headers is None else headers,
        json.dumps(data).encode() if data is not None else b"",
    )


def test_context_is_owner_only_and_uses_metadata_not_payloads(app):
    route = "/bugreport/context?session_key=selected"
    assert request(app, "GET", route, headers={})[0] == 401
    assert request(app, "GET", route, headers={"authorization": "Bearer agent"})[0] == 403
    status, context = request(app, "GET", route)
    assert status == 200
    assert "SECRET" not in json.dumps(context)
    items = {r["id"]: r["text"] for r in context["items"]}
    assert items["model"] == "example-model"
    assert items["waiting"] == "1"
    assert items["events"] == "12 ms: SessionStart"
    assert context["destination"] == "mail"
    assert request(app, "GET", "/bugreport/context")[0] == 200
    assert request(app, "GET", "/bugreport/context?session_key=missing")[0] == 404
    for query in ("session_key=", "session_key=a&session_key=b", "path=/etc/passwd"):
        assert request(app, "GET", "/bugreport/context?" + query)[0] == 400


def test_preview_bytes_survive_mailto_mime_bundle_and_removed_items(app, monkeypatch):
    body = "Only reviewed text: 🦆\r\nNo diagnostics selected.\n  trailing spaces  "
    attachment = b"immutable selected file\x00\xff"
    # Submit must not call context again or look up any runtime/connector.
    monkeypatch.setattr(bug_reports, "context", lambda *a: pytest.fail("recollected context"))
    payload = {
        "summary": "A bug 🦆",
        "body": body,
        "attachments": [
            {"name": "report.bin", "content_base64": base64.b64encode(attachment).decode()}
        ],
    }
    assert request(app, "POST", "/bugreport/submit", payload, headers={})[0] == 401
    status, result = request(app, "POST", "/bugreport/submit", payload)
    assert status == 200 and result["sent"] is False
    assert result["status"] == "draft_prepared" and not result["attachments_in_mailto"]
    assert parse_qs(urlsplit(result["mailto_url"]).query)["body"] == [body]
    assert Path(result["saved_bundle"]).stat().st_mode & 0o777 == 0o600
    with zipfile.ZipFile(result["saved_bundle"]) as archive:
        assert archive.read("report.md") == body.encode()
        draft = BytesParser(policy=policy.default).parsebytes(archive.read("draft.eml"))
        assert draft.get_payload(decode=True) == body.encode()
        assert archive.read("attachments/1-report.bin") == attachment
        assert len(archive.namelist()) == 3
    assert request(app, "GET", result["download_url"], headers={})[0] == 401

    class Writer:
        data = b""

        def write(self, data):
            self.data += data

        async def drain(self):
            pass

    async def download():
        writer = Writer()
        await app._dispatch(
            "GET", result["download_url"], None, writer, {"x-duckterm-token": app.token}, b""
        )
        head, content = writer.data.split(b"\r\n\r\n", 1)
        assert b"Cache-Control: no-store" in head
        assert content == Path(result["saved_bundle"]).read_bytes()

    asyncio.run(download())


def test_large_preview_has_complete_eml_instead_of_truncated_mailto(app):
    body = "🦆" * 5000
    status, result = request(app, "POST", "/bugreport/submit", {"summary": "Long", "body": body})
    assert status == 200 and result["mailto_url"] is None and result["sent"] is False
    with zipfile.ZipFile(result["saved_bundle"]) as archive:
        draft = BytesParser(policy=policy.default).parsebytes(archive.read("draft.eml"))
        assert draft.get_payload(decode=True) == body.encode()


@pytest.mark.parametrize(
    "extra",
    [
        {"body": "x" * (bug_reports.MAX_BODY_BYTES + 1)},
        {"summary": "header\r\ninjection"},
        {"destination": "github"},
        {"attachments": [{"path": "/etc/passwd"}]},
        {"attachments": [{"name": "../secret", "content_base64": ""}]},
        {"attachments": [{"name": "secret", "content_base64": "!"}]},
        {"attachments": [{"name": "a", "content_base64": ""}] * 6},
    ],
)
def test_invalid_reports_do_not_save_bundles(app, extra):
    before = list((bug_reports.paths.home() / "bug-reports").glob("*"))
    assert (
        request(app, "POST", "/bugreport/submit", {"summary": "Bug", "body": "reviewed", **extra})[
            0
        ]
        == 400
    )
    assert list((bug_reports.paths.home() / "bug-reports").glob("*")) == before


def test_attachment_policy_boundaries(monkeypatch):
    assert (bug_reports.MAX_FILES, bug_reports.MAX_FILE_BYTES, bug_reports.MAX_TOTAL_BYTES) == (
        5,
        5 * 1024 * 1024,
        15 * 1024 * 1024,
    )
    monkeypatch.setattr(bug_reports, "MAX_FILE_BYTES", 5)
    monkeypatch.setattr(bug_reports, "MAX_TOTAL_BYTES", 15)

    def file(size):
        return {"name": "a", "content_base64": base64.b64encode(b"x" * size).decode()}

    assert len(bug_reports.attachments([file(5)] * 3)) == 3
    with pytest.raises(ValueError):
        bug_reports.attachments([file(6)])
    with pytest.raises(ValueError):
        bug_reports.attachments([file(5)] * 4)


def test_private_reads_refuse_symlinks_fifos_and_oversize(tmp_path):
    path = tmp_path / "file"
    path.write_bytes(b"12345")
    assert bug_reports.read_regular(path, 5) == b"12345"
    with pytest.raises(ValueError):
        bug_reports.read_regular(path, 4)
    link = tmp_path / "link"
    link.symlink_to(path)
    with pytest.raises(OSError):
        bug_reports.read_regular(link, 5)
    fifo = tmp_path / "fifo"
    os.mkfifo(fifo)
    with pytest.raises(ValueError):
        bug_reports.read_regular(fifo, 5)


def test_report_body_reader_limit_is_explicit():
    async def scenario():
        reader = asyncio.StreamReader()
        reader.feed_data(b"12345")
        reader.feed_eof()
        assert await read_body(reader, {"content-length": "5"}, max_bytes=5) == b"12345"
        with pytest.raises(ValueError):
            await read_body(reader, {"content-length": "6"}, max_bytes=5)

    asyncio.run(scenario())


def test_actual_http_accepts_attachment_body_above_default_eight_mib(app):
    encoded = base64.b64encode(b"x" * (4 * 1024 * 1024)).decode()
    body = json.dumps(
        {
            "summary": "Files",
            "body": "Reviewed files",
            "attachments": [
                {"name": "a.txt", "content_base64": encoded},
                {"name": "b.txt", "content_base64": encoded},
            ],
        }
    ).encode()

    class Writer:
        data = b""

        def write(self, data):
            self.data += data

        async def drain(self):
            pass

        def close(self):
            pass

    async def scenario():
        reader = asyncio.StreamReader()
        reader.feed_data(
            (
                "POST /bugreport/submit HTTP/1.1\r\nHost: localhost\r\n"
                f"X-Duckterm-Token: {app.token}\r\nContent-Length: {len(body)}\r\n\r\n"
            ).encode()
            + body
        )
        reader.feed_eof()
        writer = Writer()
        await app.handle(reader, writer)
        assert writer.data.startswith(b"HTTP/1.1 200")
        result = json.loads(writer.data.split(b"\r\n\r\n", 1)[1])
        with zipfile.ZipFile(result["saved_bundle"]) as archive:
            assert len(archive.read("attachments/1-a.txt")) == 4 * 1024 * 1024

    asyncio.run(scenario())


def test_bundle_ids_and_symlink_are_rejected(app, tmp_path):
    assert request(app, "GET", "/bugreport/bundles/../../secret")[0] == 400
    assert request(app, "GET", "/bugreport/bundles/" + "a" * 32)[0] == 404
    _, result = request(app, "POST", "/bugreport/submit", {"summary": "Bug", "body": "Reviewed"})
    path = Path(result["saved_bundle"])
    path.unlink()
    secret = tmp_path / "secret"
    secret.write_text("must not disclose")
    path.symlink_to(secret)
    assert request(app, "GET", result["download_url"])[0] == 400


def test_recipient_is_private_configuration_and_github_stays_disabled(app, monkeypatch):
    monkeypatch.delenv("DUCKTERM_SUPPORT_EMAIL")
    assert request(app, "GET", "/bugreport/context")[1]["recipient"] == ""
    private = bug_reports.paths.home() / "support-email.txt"
    private.write_text("support@example.test\n")
    assert request(app, "GET", "/bugreport/context")[1]["recipient"] == "support@example.test"
    private.write_text("support@example.test\r\nBcc:other@example.test")
    assert request(app, "GET", "/bugreport/context")[0] == 400


def test_reviewed_version_diagnostic_includes_build_identity(app, monkeypatch):
    monkeypatch.setattr(
        bug_reports.build_info,
        "installed",
        lambda: {"installed_commit": "a" * 40, "installed_describe": "v0.4.132-2-gaaaaaaa-dirty"},
    )
    status, context = request(app, "GET", "/bugreport/context")
    version = next(row["text"] for row in context["items"] if row["id"] == "version")
    assert status == 200 and "a" * 40 in version and "v0.4.132-2-gaaaaaaa-dirty" in version
