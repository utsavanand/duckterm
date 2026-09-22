# Message folder UI proposal

Prepared for the architect and main-dev from the active UI handoff. The owner approved this preview before implementation. Preview: [Message folder](previews/message-folder.html).

The existing folder interaction-history dialog gets a **Message folder** action.
It opens a modal named **Message {folder}** with the message and an expandable recipient list. Subfolders are always
included, matching the backend target endpoint.
The submit label shows the exact number of recipients. Empty recipient lists and
blank messages disable submission. The list shows backend eligibility and skip reasons, including unavailable or
stopped sessions; the UI never independently decides who is deliverable.

The owner reviews recipients before sending. Submit creates inbox entries, never
terminal input. A successful result reports the actual recipient count. Failed
submissions keep the message text and recipients. The client provides a
stable request key so retries cannot create duplicate broadcasts. If membership
changes, the server returns the actual recipient list and per-recipient outcome.

Owner messages display **You · Owner**, derived only from an authenticated,
server-supplied sender kind, never from a sender's chosen display name. Peer
messages retain the session name. Folder history uses the same owner label and
shows the recipient session beside it.

Delivery facts remain separate from queued/accepted/answered lifecycle status:
**Not yet read**, **Notice shown**, and **Read by session** come from the v0.4.38
metadata. A shown notice does not claim that work started or that a session
accepted the request. Do not restore the discarded terminal-wake outcomes or a
needs-attention retry worker.

Backend contract from main-dev (feature/folder-broadcast): authenticated GET
`/folders/{encoded-name}/broadcast` returns `{folder,targets}`; POST accepts
`{text,request_key}` and returns `{folder,request_key,queued,skipped,results}`.
Each target has `session_id`, `name`, `state`, `eligible`, and `reason`.
Broadcast messages have `kind:broadcast`, `sender_kind:owner`, and
`requires_reply:false`. Their lifecycle is Unread → Read when the session reads
its inbox; replies are optional and no Accept control is shown. Implemented against the v0.4.39 backend; urgency and interruption remain separate follow-up work.
