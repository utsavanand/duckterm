"""Explicitly submit an already-reviewed report; keep a local mail/ZIP fallback."""

import io
import json
import re
import urllib.error
import urllib.request
import uuid
import zipfile
from email import policy
from email.parser import BytesParser
from typing import Any

from duckterm import bug_reports

REPORT_URLS = (
    "https://utsava.xyz/api/duckterm-report",
    "https://my-digital-card-three.vercel.app/api/duckterm-report",
)
MAX_UPLOAD_BYTES = 20 * 1024 * 1024
MAX_RESPONSE_BYTES = 8192


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str
    ) -> None:
        # A reporting endpoint cannot redirect private contents to another host.
        return None


def multipart(report_id: str, summary: str, body: str, archive: bytes) -> tuple[bytes, str]:
    if not isinstance(report_id, str) or not re.fullmatch(r"[a-f0-9]{32}", report_id):
        raise ValueError("Invalid report ID")
    boundary = "duckterm-" + uuid.uuid4().hex
    metadata = json.dumps(
        {"id": report_id, "summary": summary, "body": body}, ensure_ascii=False
    ).encode()
    data = (
        f'--{boundary}\r\nContent-Disposition: form-data; name="report"\r\n\r\n'.encode()
        + metadata
        + f'\r\n--{boundary}\r\nContent-Disposition: form-data; name="bundle"; '
        'filename="report.zip"\r\nContent-Type: application/zip\r\n\r\n'.encode()
        + archive
        + f"\r\n--{boundary}--\r\n".encode()
    )
    return data, f"multipart/form-data; boundary={boundary}"


def _failure(code: str, *, unconfirmed: bool = False) -> dict[str, Any]:
    messages = {
        "too_large": "Report too large to send. Save the ZIP and use email.",
        "not_configured": "Private reporting is not configured yet. Use email or save the ZIP.",
        "rate_limited": "Too many reports. Try again in an hour, or use email.",
        "result_unconfirmed": (
            "Report delivery could not be confirmed. "
            "Retry this report to check, or save the ZIP."
        ),
        "service_error": "Could not file the report. Use email or save the ZIP.",
    }
    return {
        "state": "unconfirmed" if unconfirmed else "failed",
        "code": code,
        "message": messages.get(code, messages["service_error"]),
    }


def deliver(report_id: str, summary: str, body: str, archive: bytes) -> dict[str, Any]:
    data, content_type = multipart(report_id, summary, body, archive)
    if len(data) > MAX_UPLOAD_BYTES:
        return _failure("too_large")
    opener = urllib.request.build_opener(_NoRedirect())
    uncertain = False
    for url in REPORT_URLS:
        request = urllib.request.Request(
            url, data=data, headers={"Content-Type": content_type}, method="POST"
        )
        try:
            with opener.open(request, timeout=30) as response:
                raw = response.read(MAX_RESPONSE_BYTES + 1)
                if len(raw) > MAX_RESPONSE_BYTES:
                    return _failure("result_unconfirmed", unconfirmed=True)
                value = json.loads(raw)
                number = value.get("id") if isinstance(value, dict) else None
                if (
                    isinstance(value, dict)
                    and value.get("ok") is True
                    and type(number) is int
                    and number > 0
                ):
                    return {"state": "filed", "issue_number": number}
                return _failure("result_unconfirmed", unconfirmed=True)
        except urllib.error.HTTPError as exc:
            # A service refusal must not be bypassed by trying the second host.
            # Network failures may follow a completed write; keep that ambiguity.
            uncertain = uncertain or exc.code == 409 or (exc.code >= 500 and exc.code != 503)
            code = {413: "too_large", 429: "rate_limited", 503: "not_configured"}.get(
                exc.code, "service_error"
            )
            exc.close()
            if uncertain:
                return _failure("result_unconfirmed", unconfirmed=True)
            return _failure(code, unconfirmed=uncertain)
        except (urllib.error.URLError, TimeoutError, OSError):
            uncertain = True
            continue
        except (ValueError, TypeError, AttributeError):
            return _failure("result_unconfirmed", unconfirmed=True)
    return _failure("result_unconfirmed", unconfirmed=True)


def file_report(request: Any) -> dict[str, Any]:
    if not isinstance(request, dict) or set(request) != {"id"}:
        raise ValueError("Expected a prepared report ID")
    report_id = request["id"]
    if not isinstance(report_id, str) or not re.fullmatch(r"[a-f0-9]{32}", report_id):
        raise ValueError("Invalid report ID")
    # The immutable export is the submission snapshot, including on retries.
    # Never recollect diagnostics or rebuild a ZIP with different timestamps.
    archive = bug_reports.bundle(report_id)
    try:
        with zipfile.ZipFile(io.BytesIO(archive)) as saved:
            for name, limit in (
                ("report.md", bug_reports.MAX_BODY_BYTES),
                ("draft.eml", 4 * bug_reports.MAX_BODY_BYTES),
            ):
                if saved.getinfo(name).file_size > limit:
                    raise ValueError("Invalid prepared report")
            body = saved.read("report.md").decode("utf-8")
            draft = BytesParser(policy=policy.default).parsebytes(
                saved.read("draft.eml"), headersonly=True
            )
            summary = str(draft.get("Subject", ""))
    except (zipfile.BadZipFile, KeyError, UnicodeError) as exc:
        raise ValueError("Invalid prepared report archive") from exc
    return deliver(report_id, summary, body, archive)
