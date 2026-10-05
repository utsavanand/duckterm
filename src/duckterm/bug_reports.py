"""Owner-reviewed diagnostics and local mail bundles; never sends a report."""

import base64
import binascii
import io
import os
import platform
import re
import stat
import tempfile
import urllib.parse
import uuid
import zipfile
from email.message import EmailMessage
from email.policy import SMTP
from pathlib import Path
from typing import Any

from duckterm import __version__
from duckterm.core import events
from duckterm.helpers import paths
from duckterm.persistence.history import HistoryStore

MAX_FILES = 5
MAX_FILE_BYTES = 5 * 1024 * 1024
MAX_TOTAL_BYTES = 15 * 1024 * 1024
MAX_BODY_BYTES = 64 * 1024
MAX_REQUEST_BYTES = 21 * 1024 * 1024
MAX_BUNDLE_BYTES = MAX_TOTAL_BYTES + 4 * MAX_BODY_BYTES
MAILTO_LIMIT = 8000


def read_regular(path: Path, limit: int) -> bytes:
    """Bound reads after opening; refuse symlinks and non-regular files."""
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
            raise ValueError("Expected a regular file owned by this user")
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise ValueError("File exceeds its size limit")
    return data


def recipient() -> str:
    value = os.environ.get("DUCKTERM_SUPPORT_EMAIL")
    if value is None:
        try:
            value = read_regular(paths.home() / "support-email.txt", 254).decode("utf-8")
        except FileNotFoundError:
            value = ""
    value = value.strip()
    if len(value) > 254 or any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise ValueError("Invalid support email configuration")
    if value and not re.fullmatch(r"[^\s@<>?,;:]+@[^\s@<>?,;:]+", value):
        raise ValueError("Invalid support email configuration")
    return value


def context(history: HistoryStore, session_key: str | None) -> dict[str, Any]:
    items = [
        {"id": "version", "label": "DuckTerm version", "text": __version__},
        {
            "id": "schema",
            "label": "Database schema",
            "text": str(history._conn.execute("PRAGMA user_version").fetchone()[0]),
        },
        {"id": "os", "label": "Server OS", "text": platform.system() + " " + platform.release()},
    ]
    if session_key is not None:
        row = history.session(session_key)
        if row is None:
            raise LookupError("Session not found")
        harness = str(row.get("runtime") or "generic")[:128]
        model = str(row.get("model") or "Not recorded")[:256]
        items.extend(
            [
                {"id": "harness", "label": "Session harness", "text": harness},
                {"id": "model", "label": "Recorded model", "text": model},
                {
                    "id": "state",
                    "label": "Session state",
                    "text": str(row.get("state") or "unknown"),
                },
            ]
        )
        # Query only canonical event metadata, never deserialize hook payloads.
        marks = ",".join("?" for _ in events.ALL)
        recent = history._conn.execute(
            f"SELECT event_type, ts FROM events WHERE session_key = ? "
            f"AND event_type IN ({marks}) ORDER BY ts DESC LIMIT 20",
            (session_key, *events.ALL),
        ).fetchall()
        items.append(
            {
                "id": "events",
                "label": "Recent event types and timestamps",
                "text": "\n".join(f"{r['ts']} ms: {r['event_type']}" for r in reversed(recent)),
            }
        )
        waiting = history._conn.execute(
            "SELECT count(*) FROM sessions WHERE state = 'waiting' AND runtime = ? "
            "AND session_key != ?",
            (row.get("runtime"), session_key),
        ).fetchone()[0]
        items.append(
            {
                "id": "waiting",
                "label": "Other waiting sessions with this harness",
                "text": str(waiting),
            }
        )
    return {
        "items": items,
        "recipient": recipient(),
        "destination": "mail",
        "limits": {
            "attachments": MAX_FILES,
            "file_bytes": MAX_FILE_BYTES,
            "total_bytes": MAX_TOTAL_BYTES,
            "body_bytes": MAX_BODY_BYTES,
        },
    }


def attachments(value: Any) -> list[tuple[str, bytes]]:
    if not isinstance(value, list) or len(value) > MAX_FILES:
        raise ValueError("Choose at most five attachments")
    result = []
    total = 0
    for item in value:
        if not isinstance(item, dict) or set(item) != {"name", "content_base64"}:
            raise ValueError("Attachments require a name and immutable base64 bytes")
        name, encoded = item["name"], item["content_base64"]
        if (
            not isinstance(name, str)
            or not name
            or len(name) > 200
            or name in {".", ".."}
            or any(c in name for c in "/\\")
            or any(ord(c) < 32 or ord(c) == 127 for c in name)
        ):
            raise ValueError("Invalid attachment name")
        if not isinstance(encoded, str) or len(encoded) > 4 * ((MAX_FILE_BYTES + 2) // 3):
            raise ValueError("Attachment exceeds five MiB")
        try:
            data = base64.b64decode(encoded, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise ValueError("Invalid attachment bytes") from exc
        total += len(data)
        if len(data) > MAX_FILE_BYTES or total > MAX_TOTAL_BYTES:
            raise ValueError("Attachments exceed the five MiB/file or fifteen MiB total limit")
        result.append((name, data))
    return result


def prepare(request: Any) -> dict[str, Any]:
    if not isinstance(request, dict) or set(request) - {"summary", "body", "attachments"}:
        raise ValueError("Expected summary, body and optional attachments")
    summary, body = request.get("summary"), request.get("body")
    if (
        not isinstance(summary, str)
        or not summary.strip()
        or len(summary) > 200
        or any(ord(c) < 32 or ord(c) == 127 for c in summary)
    ):
        raise ValueError("A single-line summary of at most 200 characters is required")
    if not isinstance(body, str) or not body.strip() or len(body.encode()) > MAX_BODY_BYTES:
        raise ValueError("A report body of at most 64 KiB is required")
    files = attachments(request.get("attachments", []))
    address = recipient()
    mailto = (
        "mailto:"
        + urllib.parse.quote(address, safe="@")
        + "?"
        + urllib.parse.urlencode(
            {"subject": summary, "body": body},
            quote_via=urllib.parse.quote,
        )
    )
    # MIME decoding reproduces exactly the reviewed UTF-8 bytes, even CRLF/no final newline.
    draft = EmailMessage(policy=SMTP)
    if address:
        draft["To"] = address
    draft["Subject"] = summary
    draft["MIME-Version"] = "1.0"
    draft["Content-Type"] = "text/plain; charset=utf-8"
    draft["Content-Transfer-Encoding"] = "base64"
    draft.set_payload(base64.encodebytes(body.encode()).decode("ascii"))
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_STORED) as archive:
        archive.writestr("report.md", body.encode())
        archive.writestr("draft.eml", draft.as_bytes())
        for index, (name, data) in enumerate(files, 1):
            archive.writestr(f"attachments/{index}-{name}", data)
    data = buffer.getvalue()
    if len(data) > MAX_BUNDLE_BYTES:
        raise ValueError("Report bundle is too large")
    directory = paths.home() / "bug-reports"
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    if directory.is_symlink() or not directory.is_dir():
        raise ValueError("Invalid report directory")
    report_id = uuid.uuid4().hex
    destination = directory / (report_id + ".zip")
    fd, temporary = tempfile.mkstemp(prefix=".report-", dir=directory)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return {
        "status": "draft_prepared",
        "sent": False,
        "mailto_url": mailto if len(mailto) <= MAILTO_LIMIT else None,
        "recipient": address,
        "saved_bundle": str(destination),
        "download_url": f"/bugreport/bundles/{report_id}",
        "attachments_in_mailto": False,
        "notice": (
            "Open the mail draft, attach files from the downloaded ZIP, and send it yourself."
            if len(mailto) <= MAILTO_LIMIT
            else "This report is too long for a mail link. Download the ZIP and open draft.eml."
        ),
    }


def bundle(report_id: str) -> bytes:
    if not re.fullmatch("[a-f0-9]{32}", report_id):
        raise ValueError("Invalid report ID")
    directory = paths.home() / "bug-reports"
    if directory.is_symlink():
        raise ValueError("Invalid report directory")
    return read_regular(directory / (report_id + ".zip"), MAX_BUNDLE_BYTES)
