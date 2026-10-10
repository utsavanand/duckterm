# Dashboard bug-report API

The dashboard prepares a local mail draft and ZIP. It never sends mail, uploads
to GitHub, or reads credentials from a connector. The existing native Mac bug
reporter, including screenshot capture, remains available.

Every endpoint requires the owner token. Agent credentials are refused. Remote
clients must route requests to the selected server through the existing host
transport. Diagnostics describe that server; attachments are bytes selected on
the client. Responses use no-store caching.

## Collect removable context

GET /bugreport/context?session_key=KEY

Omit session_key for a report without a selected session. An unknown session
returns 404; empty or duplicate keys return 400. The response contains:

- items: objects with id, label and text. Each is independently removable.
- recipient: configured support email, or an empty string.
- destination: mail.
- limits: attachments=5, file_bytes=5242880, total_bytes=15728640,
  body_bytes=65536.

Items include version, actual database schema version, server OS, and when a
session is selected its recorded harness/model/state, the latest 20 canonical
event types and timestamps, and a count of other waiting sessions using that
harness. A `resume-readiness` item also summarizes up to 50 interrupted/stopped
sessions (selected session first), with the total and shown counts. Rows use
anonymous ordinal labels, not session names or keys. Each reports runtime,
state, native-ID presence (`missing`, `empty-string`, `present`, `invalid`, or
`unknown`), working-directory source/existence, and expected transcript-file
existence. Claude project slugs are represented only by a stable SHA-256 fingerprint
(first 16 hex characters), so project/user names are not exposed. This correlates
equal expected project locations without exporting the slug or absolute path. No
absolute paths, conversation IDs, transcript filenames, contents or credentials
are emitted. Codex uses its existing filename lookup; other harnesses explicitly
report that per-conversation file lookup was not checked.

The item reports jq availability and DuckTerm hook configuration per harness,
both global and the row's project configuration. Configured does not establish
runtime trust or successful delivery. Invalid/unreadable configuration reports
unknown. SQLite metadata is gathered on its owning thread; file checks run off
the event loop. Only ID presence is exported from the stored event ID field;
event payload contents, terminal output, transcripts, raw logs and other-session
content are excluded. This snapshot does not diagnose which condition caused a
previous failed attempt. Submit still uses exactly the bytes the owner reviewed.
The native report must retrieve and append this item through its own integration;
the backend change alone does not change native ZIP contents.

Set DUCKTERM_SUPPORT_EMAIL in the server environment, or save a private
support-email.txt under DUCKTERM_HOME. No personal recipient is stored in the
repository. An empty recipient leaves the mail draft unaddressed; the UI must
say so. This does not change the native app's support-email configuration.

## Prepare exactly what was reviewed

POST /bugreport/submit with JSON:

    {
      "summary": "Short single-line subject",
      "body": "The exact Markdown text displayed in the preview",
      "attachments": [
        {"name": "screenshot.png", "content_base64": "..."}
      ]
    }

Attachments are optional. The client reads and previews selected bytes, then
submits those same bytes; the server accepts no filesystem source paths.
At most five files, five MiB each and fifteen MiB total. The body is limited
to 64 KiB UTF-8; the summary to 200 characters without control characters.
Names must be plain filenames. Unknown submission fields, including a GitHub
destination, are refused.

The server does not recollect context. report.md and the decoded body in
draft.eml contain exactly the supplied body bytes, including whitespace and
line endings. The ZIP includes only those two files and submitted attachments,
with numbered attachment names. It adds no removed diagnostics.

A successful response is:

    {
      "status": "draft_prepared",
      "sent": false,
      "mailto_url": "mailto:...",
      "recipient": "...",
      "saved_bundle": "/server/private/path/report-id.zip",
      "download_url": "/bugreport/bundles/report-id",
      "attachments_in_mailto": false,
      "notice": "..."
    }

Opening mailto creates a draft, not delivery. Files must be attached manually
from the downloaded ZIP. If the encoded URL would exceed 8000 characters,
mailto_url is null; the UI offers the ZIP containing the complete draft.eml
instead. It must not truncate the body or claim a message was sent.

## Download the bundle

GET /bugreport/bundles/REPORT_ID

Fetch using owner-authenticated host transport and download the returned ZIP
as a Blob. A server filesystem path alone is insufficient for remote clients.
IDs are 32 lowercase hexadecimal characters; paths are not accepted. Reads
use O_NOFOLLOW, verify the opened file is regular and user-owned, and are
bounded. ZIPs are saved atomically with mode 0600 under bug-reports in the
server's private home. They persist until the owner removes them; v1 has no
automatic deletion or background upload.

The dashboard UI and native integration checks remain with UI-dev. Backend
tests use synthetic metadata/files and do not submit a real report.
