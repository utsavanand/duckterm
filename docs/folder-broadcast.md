# Folder broadcast backend

The owner can send one message to every enrolled session in a sidebar folder
and its descendants. Busy and stopped sessions receive a queued notice.
Unenrolled or private sessions are skipped with a reason. No terminal is touched.

Both routes require the owner token. Agent bearer credentials receive 403.
The folder segment must be URL-encoded; unknown folders receive 404.

- `GET /folders/{folder}/broadcast` previews `{folder, targets}`. Each target has
  `session_id, name, state, eligible, reason`.
- `POST /folders/{folder}/broadcast` takes `{text, request_key?}` and returns 202
  with `{folder, request_key, queued, skipped, results}`. Each result adds
  `status: queued|skipped` and, when queued, `message_id`.
- Text is limited to 16 KiB. Reuse a request key when retrying; the recorded
  result is returned without duplicating delivery. Reusing it for different
  content or another folder receives 409. Retry records last seven days.

Inbox records use `kind: broadcast`, `sender: owner`, `sender_kind: owner`,
`sender_name: You`, and `requires_reply: false`. These fields are assigned by
the owner-only endpoint, never accepted from a peer question. Consumers should
use `sender_kind` to distinguish owner messages from similarly named agents.

Agent inbox reads mark queued broadcasts `read`; dashboard previews/history
do not. Accept and decline are unnecessary and rejected; a recipient may still
reply through the existing question answer endpoint. Other sessions cannot
read or answer its copy. Owner notices do not consume peer question rate or
pending budgets. They are retained for seven days from delivery, rather than
expiring on the question deadline. Their public `expires_at` is zero.

Supported task-end hooks remind agents of unread owner notices once per record.
Waiting/approval and loop safeguards remain in effect. Reading a notice is not
proof its requested work is finished. An agent already idle is not awakened.

Moving an entire shared folder preserves its notices; moving an individual
recipient outside the original root cancels its unread notices. Stopping a
session preserves queued notices until resume.

The Message folder interface and owner inbox presentation belong to UI-dev.
The backend can be integrated independently; this change adds no visual controls.
