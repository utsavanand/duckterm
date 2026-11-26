# Private bug report delivery

Status: the approved Send report wording and native/browser submission flows are
implemented locally. No endpoint has been deployed and no real reports have been
uploaded by these tests.

The approved scope is to keep DuckTerm's existing report fields, screenshot,
removable diagnostics and attachment selection. The delivery target changes from
an email composer to a private GitHub issue. Email and ZIP export stay available.
An explicit Send report action is required; preparing or saving a report must
never upload it.

## Components

- `bug_reports.prepare` remains local-only. Existing clients calling
  `/bugreport/submit` still prepare an email/ZIP, so upgrading the server cannot
  make an old client's Prepare mail draft button transmit private data.
- `/bugreport/file` is a new owner-authenticated explicit submission route. It
  accepts a prepared report ID, reads that exact saved ZIP, and sends it with
  its report text to the reporting service. It never recollects context.
- `BugReportDelivery.swift` sends the same frozen native report to the primary
  host, then the fallback only on network failure. Neither client has a GitHub
  token. Redirects are refused; response sizes and upload sizes are bounded.
- `api/duckterm-report.js` in `my-digital-card` is the service. It validates the
  request and private repository, saves the complete ZIP privately, and creates
  the issue. The issue shows a bounded excerpt and links the complete ZIP.

Production addresses:

- `https://utsava.xyz/api/duckterm-report`
- `https://my-digital-card-three.vercel.app/api/duckterm-report`

Reports go to `utsavanand/duckterm-reports`. Missing setup, a public destination,
rate limiting, or upstream failure never causes a report to be posted publicly.
The endpoint returns only a confirmed issue number, not a private-content URL.

## Retry contract

The immutable report ID and ZIP are retained for retries while the report form
remains open. Editing the contents creates a new submission on the next send.
Closing and reopening the form does not recover the prior delivery ID. A repeated upload
cannot replace an existing bundle or create a second issue. A lost response can
be checked again using the same ID. If the server cannot confirm the result, the
UI must say so and preserve export; it must not invent either success or failure.

GitHub assets and issue creation are separate operations. A crash between them
can leave a private asset with no issue. The service conservatively retains that
asset and reports unconfirmed. Maintainers can inspect it; the user can export or
email their local copy. This is not an exactly-once transactional guarantee.

## Release prerequisites

The website's `docs/duckterm-reports.md` contains repository, token, Vercel body
limit, WAF, notification and recovery instructions. A separate fine-grained
`DUCKTERM_REPORTS_TOKEN` needs Issues and Contents write permissions on only the
private reports repository. Contents permits the private ZIP asset. Do not reuse
Kaho's token or embed a token in a client build.

No production deployment, credential setup, or GitHub publication is included in
the local implementation. Before release, verify both hostnames with synthetic
content, private issue and asset access, byte-identical ZIP download, retry
receipts, the native app flow, and the existing email/ZIP fallback.

## Verification and rollout

Focused checks cover the owner-only route, preserved binary attachments, invalid
and corrupt local bundles, service refusals, bounded responses, native/browser
explicit consent, duplicate-submit prevention, retry identity, and exact ZIP
export. The native WKWebView test uses mocked delivery in temporary windows;
it exercises actual compression and the existing diagnostic opt-out. It does
not change the installed app or send email.

Release the service first and verify synthetic submissions on both hostnames.
Then release the app through release-dev. Until service setup is complete, the
app reports that filing is unavailable and retains email/ZIP export; do not
advertise private filing as live before the deployed verification succeeds.
