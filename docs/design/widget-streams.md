# Built-in widget streams

Widget code stays in the shipped web bundle. Instances contain only id, type,
slot, position and parameters. Oracle types cannot be placed in folder slots.
Layouts are owner-authenticated GET/PUT `/layouts/:surface`, stored privately in
`layouts.json`, with revision checks and recoverable folder rename/delete intents.
The default Oracle composition is Agents, Needs you, Tokens, Agent mail, Last
backup and Remote sessions. Tile contents/styles are retained within the approved
chat-left layout. Folder Chat and Artifacts remain application views, not widgets.

Each rendered widget subscribes only to its declared stream names. The existing
60-second `/control-tower` read is shared by token/mail/backup/remote subscribers;
removing the last subscriber cancels its timer, and late results are discarded.
Sessions use the existing dashboard stream; needs-you uses the existing relay
reader shared with the chat. No additional polling cadence is introduced.
An absent stream, failed request, or unsupported remote source renders Unavailable.

## Analytics conformance checklist

These are data families, not extra transports. Analytics keeps its current APIs;
widgets take projections from those same families.

| Every rendered value or grouping | Stream | Existing backing |
| --- | --- | --- |
| Total, input, cache read, cache write, output; daily average; busiest day; cache rate | tokens | token ledger, `/analytics/tokens`; `/control-tower` seven-day projection |
| Token day/model/agent/session/folder breakdowns and selector options; UTC range, earliest day, coverage | tokens | `/analytics/tokens` rows and metadata |
| Questions sent/answered/declined/expired/cancelled by day; median and slowest answer times | mail | durable `/analytics/mail` daily rollups |
| Sender/recipient pair rankings and top senders/recipients | mail | `/analytics/mail` top_pairs and participant rollups |
| Broadcast recipient deliveries, Oracle nudges by day | mail | `/analytics/mail` daily rollups; 24-hour tile projection |
| Open queued/accepted totals and oldest pending messages, sender/recipient/status/time | mail | `/analytics/mail` open_now snapshot |
| Analytics tracking start, timezone, approximation/coverage notes | mail | `/analytics/mail` metadata |
| Session names used in analytics labels, current state, team counts | sessions | dashboard session stream |
| Needs-you count, oldest note, class and age | needs-you | `/relay`; existing question urgency retained; approvals labeled on the wire |
| Destination kind, result, finish time and backup age | backup | existing backup status projection |
| Remote count and source availability | remote | existing control-tower source; unavailable on servers without it |
| Folder activity and artifact kinds | folder-stats / artifacts-by-kind | folder-specific types; absent until their respective endpoint is installed |

No Analytics value requires fetching transcript bodies, inbox message content, or
an undeclared widget stream. Widget stream values are read-only; layout mutation
changes placement only. Existing Oracle chat and agent messaging stay outside the
widget registry.
