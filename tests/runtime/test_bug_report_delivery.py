"""Submission uses the saved snapshot, never recollects data or sends email."""

import base64
import io
import json
import urllib.error
import urllib.request
import zipfile
from email import policy
from email.message import Message
from email.parser import BytesParser

import pytest
from tests.runtime import test_bug_reports
from tests.runtime.test_bug_reports import request

from duckterm import bug_report_delivery as delivery
from duckterm import bug_reports


@pytest.fixture
def app(tmp_path, monkeypatch):
    yield from test_bug_reports.app.__wrapped__(tmp_path, monkeypatch)


class Response(io.BytesIO):
    pass


class Opener:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.requests = []

    def open(self, req, timeout):
        assert timeout == 30
        self.requests.append(req)
        value = self.outcomes.pop(0)
        if isinstance(value, Exception):
            raise value
        return Response(value)


def fake(monkeypatch, outcomes):
    opener = Opener(outcomes)
    monkeypatch.setattr(delivery.urllib.request, "build_opener", lambda *args: opener)
    return opener


def test_native_equivalent_multipart_preserves_binary_bytes():
    archive = b"PK\x03\x04\x00\xff\n"
    data, content_type = delivery.multipart("a" * 32, "Report 🦆", "selected text", archive)
    mail = BytesParser(policy=policy.default).parsebytes(
        f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n".encode() + data
    )
    parts = list(mail.iter_parts())
    assert json.loads(parts[0].get_payload(decode=True)) == {
        "id": "a" * 32,
        "summary": "Report 🦆",
        "body": "selected text",
    }
    assert parts[1].get_payload(decode=True) == archive


def test_network_fallback_reuses_snapshot_and_id(monkeypatch):
    opener = fake(monkeypatch, [urllib.error.URLError("dns"), b'{"ok":true,"id":42}'])
    assert delivery.deliver("a" * 32, "Bug", "Reviewed", b"PK\x03\x04") == {
        "state": "filed",
        "issue_number": 42,
    }
    assert [r.full_url for r in opener.requests] == list(delivery.REPORT_URLS)
    assert opener.requests[0].data == opener.requests[1].data
    assert all(r.get_header("Authorization") is None for r in opener.requests)


@pytest.mark.parametrize("status", [400, 409, 413, 429, 502, 503])
def test_service_refusal_is_not_retried_or_exposed(monkeypatch, status):
    error = urllib.error.HTTPError("https://service", status, "secret", {}, io.BytesIO(b"secret"))
    opener = fake(monkeypatch, [error])
    result = delivery.deliver("a" * 32, "Bug", "Reviewed", b"PK\x03\x04")
    assert result["state"] != "filed"
    assert "secret" not in json.dumps(result)
    assert len(opener.requests) == 1


@pytest.mark.parametrize(
    "body",
    [
        b"[]",
        b"{}",
        b'{"ok":true,"id":true}',
        b'{"ok":true,"id":1.5}',
        b'{"ok":true,"id":-1}',
        b"x" * 8193,
    ],
)
def test_unusable_success_receipt_is_unconfirmed(monkeypatch, body):
    fake(monkeypatch, [body])
    assert delivery.deliver("a" * 32, "Bug", "Reviewed", b"PK\x03\x04")["state"] == "unconfirmed"


def test_oversized_report_is_kept_local(monkeypatch):
    opener = fake(monkeypatch, [])
    monkeypatch.setattr(delivery, "MAX_UPLOAD_BYTES", 4)
    assert delivery.deliver("a" * 32, "Bug", "Reviewed", b"PK\x03\x04")["code"] == "too_large"
    assert not opener.requests


def test_redirects_cannot_forward_private_report():
    opened = []

    class Redirect(urllib.request.HTTPSHandler):
        def https_open(self, request):
            opened.append(request.full_url)
            assert request.full_url == "https://reports.invalid"
            headers = Message()
            headers["Location"] = "https://other.invalid"
            response = urllib.response.addinfourl(io.BytesIO(), headers, request.full_url, 307)
            response.msg = "Temporary Redirect"
            return response

    opener = urllib.request.build_opener(delivery._NoRedirect(), Redirect())
    request = urllib.request.Request("https://reports.invalid", data=b"private report")
    with pytest.raises(urllib.error.HTTPError) as error:
        opener.open(request)
    assert error.value.code == 307
    assert opened == ["https://reports.invalid"]


def test_filing_is_explicit_owner_only_and_reads_exact_saved_bundle(app, monkeypatch):
    opener = fake(monkeypatch, [b'{"ok":true,"id":42}', b'{"ok":true,"id":42}'])
    payload = {
        "summary": "Selected 🦆",
        "body": "Only reviewed text\r\n",
        "attachments": [
            {
                "name": "selected.bin",
                "content_base64": base64.b64encode(b"selected\x00\xff").decode(),
            }
        ],
    }
    status, draft = request(app, "POST", "/bugreport/submit", payload)
    assert status == 200 and not opener.requests
    report_id = draft["download_url"].rsplit("/", 1)[1]
    before = bug_reports.bundle(report_id)
    monkeypatch.setattr(bug_reports, "context", lambda *a: pytest.fail("Recollected diagnostics"))
    monkeypatch.setattr(
        bug_reports, "prepare", lambda *a: pytest.fail("Rebuilt the saved snapshot")
    )
    assert request(app, "POST", "/bugreport/file", {"id": report_id}, headers={})[0] == 401
    assert not opener.requests
    for _ in range(2):
        status, result = request(app, "POST", "/bugreport/file", {"id": report_id})
        assert status == 200 and result == {"state": "filed", "issue_number": 42}
    assert bug_reports.bundle(report_id) == before
    for req in opener.requests:
        assert before in req.data
    with zipfile.ZipFile(io.BytesIO(before)) as bundle:
        assert bundle.read("report.md") == payload["body"].encode()
        assert bundle.read("attachments/1-selected.bin") == b"selected\x00\xff"


def test_invalid_submission_never_sends(app, monkeypatch):
    opener = fake(monkeypatch, [])
    for payload in ({}, {"id": "../escape"}, {"id": "a" * 32, "body": "unreviewed"}):
        assert request(app, "POST", "/bugreport/file", payload)[0] == 400
    assert not opener.requests


@pytest.mark.parametrize("kind", ["not_zip", "missing_metadata", "bad_crc"])
def test_corrupt_saved_bundle_is_rejected_without_submission(monkeypatch, kind):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        archive.writestr("report.md", "original body")
        if kind != "missing_metadata":
            archive.writestr("draft.eml", "Subject: Bug\r\n\r\n")
    data = stream.getvalue()
    if kind == "not_zip":
        data = b"not a zip"
    elif kind == "bad_crc":
        data = data.replace(b"original body", b"modified body")
    monkeypatch.setattr(bug_reports, "bundle", lambda report_id: data)
    opener = fake(monkeypatch, [])
    with pytest.raises(ValueError, match="Invalid prepared report archive"):
        delivery.file_report({"id": "a" * 32})
    assert not opener.requests
