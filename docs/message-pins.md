# Message bookmarks

A session can retain individual messages as pins. Pinning does not send input
or change the transcript, so it works while an agent is running or stopped.

## API contract

`GET /sessions/:key/messages` adds an opaque `message_key` to each existing
`{id, role, blocks}` record. Keep the numeric `id` for rendering, but use
`message_key` for pinning and navigation. The key checks the runtime,
conversation ID, record position, and content. It remains stable as messages
are appended. A rewritten record cannot silently become a pin's new target.

All pin routes require `X-Duckterm-Token`; agent bearer credentials are refused.

- `GET /sessions/:key/pins` returns `{pins: [{message_key, message, created_at}]}`,
  oldest pin first. `message` is the saved complete structured message,
  including its `message_key`; timestamps are milliseconds since epoch.
- `POST /sessions/:key/pins` with `{message_key}` returns `{pin: ...}` (200).
  The server reads the message itself, rather than trusting client content.
  Repeated requests return the same pin and original creation timestamp.
- `DELETE /sessions/:key/pins/:message_key` returns `{removed: true}` (200),
  including when already absent. Deletion is scoped to that session.

Missing sessions return 404. A stale selection returns 409: refresh Messages
rather than bookmarking another record. Invalid bodies return 400. Each
session allows 100 pins (409 at the limit); one snapshot may be at most 1 MiB
(413). Saved pins remain available when their source transcript is unavailable.
Deleting a session, or purging a test session, removes its pins.

## Using pins

Use the Pin/Unpin action per message and the compact strip above Terminal with
short text excerpts. Clicking switches to Messages, finds the exact
`message_key`, selects the containing turn and scrolls to that message. Never
navigate by the old numeric ID alone. If the key is absent, display the saved
message with an explicit saved-copy label rather than jumping elsewhere.
Render snapshots through the same safe message renderer. Pin and unpin while
running without stealing terminal focus or submitting terminal input.

Pins refresh after mutation and when the selected session changes. The approved
preview is recorded in docs/previews/message-bookmarks.html.
