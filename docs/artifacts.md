# Local artifacts

Generated deliverables are registered by their producing agent and saved locally
in DuckTerm. The Artifacts tab sits next to Inbox and follows the selected
session. The backend does not scan the filesystem or import historical files.

## Agent registration

```sh
duckterm session artifact /absolute/path/report.md --title "Release review"
duckterm session artifacts
```

The CLI reads the file on the agent's host and sends its bytes with that session's
credential. The server never opens the supplied path. Instructions included at
launch/resume, and in `session self`/`session inbox` output, tell agents to register
new or updated user-facing deliverables before replying. This is cooperative
registration, not guaranteed detection of every file an agent writes. Already
running agents receive the reminder on their next self/inbox read.

Each session/path pair has a stable artifact ID. Re-registering it updates its
saved copy; unchanged retries are idempotent. The source path is provenance, not
a live link. No history of prior versions is retained in v1. Removing a saved
artifact never deletes the source file. Deleting a session deletes its artifacts;
test-session cleanup also removes them. SQLite backup includes saved contents.
There is no automatic expiration or cloud publication.

Files are limited to 5 MiB each, 200 files / 100 MiB per session, and 500 MiB total.
Limits fail explicitly without replacing an existing snapshot. Local filesystem
symlinks and nonregular files are rejected by the CLI. Text must be UTF-8.
Markdown, HTML, text, PNG/JPEG/GIF/WebP/SVG, and PDF have declared media types;
other extensions are downloadable binary files. The UI must show unsupported
preview types honestly. HTML is a static isolated preview in v1; artifact scripts and
network access must be disabled. Relative assets are not bundled automatically.

## API contract

Agent credential only:

- GET `/api/v1/session/artifacts` → `{artifacts: [...]}` for the caller only.
- POST same URL with exactly `{title, source_path, content_base64}` → `{artifact}`.
  The authenticated session determines ownership; a session ID in the body is
  rejected. A client cannot register on behalf of another session.

Owner credential required even for reads:

- GET `/sessions/:key/artifacts` → `{artifacts: [...]}`.
- GET `/sessions/:key/artifacts/:id` → `{artifact}` with `content_base64`.
- DELETE same detail URL → `{removed: true}` (idempotent, session-scoped).

Metadata: `id`, `session_key`, `title`, `source_path`, `media_type`, `size`,
`sha256`, `created_at`, `updated_at` (timestamps in milliseconds). List and POST
responses omit content. Cross-session content lookups return 404. Agent bearer
credentials cannot access owner endpoints. Content is returned only inside JSON,
never served as active HTML on the dashboard origin. Frontend previews must keep
that isolation and sanitize rendered Markdown. DuckCentral/cloud sync is deferred.

The owner approved the layout in `docs/previews/artifacts.html`. The app follows
the selected session, refreshes registered outputs while the tab is open, and
provides saved-copy download/removal. Downloads in the Mac shell use a native
Save dialog, stage privately, then write atomically to the chosen destination.

Preview isolation follows [MDN srcdoc guidance](https://developer.mozilla.org/en-US/docs/Web/API/HTMLIFrameElement/srcdoc):
an opaque-origin iframe (never `allow-same-origin`), sanitized HTML, and a restrictive CSP
inserted before user content. Selection feedback uses `allow-scripts` only for an
app-owned selection reporter, authorized with a fresh random CSP nonce. All
artifact scripts and nonce attributes are stripped. Parent messages must match
the current frame, opaque origin, channel and selection shape before opening a
feedback box; a frame message never submits feedback. See [MDN script-src](https://developer.mozilla.org/en-US/docs/Web/HTTP/Reference/Headers/Content-Security-Policy/script-src). Downloads use Apple's
[WKDownloadDelegate](https://developer.apple.com/documentation/webkit/wkdownloaddelegate).
The browser regression verifies resource blocking, script isolation, snapshot
updates, downloads, reload persistence, removal, and terminal-draft preservation.


Native download verification: `scripts/test_artifact_download.sh` exercises a real
Mac WebKit blob download with the production save delegate, byte-for-byte output
and atomic replacement of an isolated test file. It requires macOS.

## Feedback to the producing session

Highlight text in Markdown, static HTML or plain-text previews to open a quoted
comment. The Feedback button opens a whole-artifact comment, including for images
and downloadable formats. Send (or Command/Ctrl+Enter) delivers it to the artifact's
session through the same terminal input path as Messages feedback; Cancel/Escape
closes it. The artifact title, source path, ID, revision hash, selected text and
comment accompany the feedback. This explicitly submits a follow-up prompt.

Owner-authenticated POST `/sessions/:key/annotations` accepts `artifact_id`,
`artifact_sha256`, `quote` and `note`. Note is required; quote may be empty. Quote
and note each allow up to 8000 UTF-8 bytes. The server resolves the artifact within
that session and rejects stale revisions. Terminal delivery encodes all fields as
one JSON line so artifact text cannot inject control keystrokes or extra submits.
Successful deliveries are recorded in the session's annotation history. A stopped
agent, changed artifact, or write failure returns an error; the UI keeps the
comment and never claims it was sent. A retained comment stays bound to the
original revision until the owner cancels it and selects the updated content.

Native selection verification: after running `web/e2e/artifact-feedback.spec.ts`,
run `scripts/test_artifact_feedback.sh` on macOS. It loads the actual sanitized
preview emitted by that browser test into WKWebView and checks selected text,
opaque-origin isolation and blocked artifact scripts. Its fixture contains only
test artifact content and is written to `/tmp/duckterm-feedback-native.json`.
