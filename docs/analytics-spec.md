# Analytics: tokens and Agent Mail — spec

Status: proposed by product for the owner, 2026-09-28. Not designed or built.
Owner's ask: click the Oracle page's Tokens tile and the Agent Mail tile to
get "deeper analytics … a small kind of analytics app within DuckTerm".

## Owner decisions (2026-09-28)

1. **Clicking a tile opens a separate Analytics page.** The tile can
   expand into the page as the transition, but the analytics live on their
   own page, not inside the Oracle page.
2. **No dollar cost.** The owner is on a subscription; keep cost out of this
   page. If it's ever wanted, it's a separate feature.
3. **Keep mail counts forever.**

## Summary

Clicking **Tokens** or **Agent Mail** on the Oracle page opens an
**Analytics** page with two tabs, a time range picker, and charts. Most of
the token data is already collected; Agent Mail needs a small daily rollup
because messages are deleted after 7 days.

## What exists today

| Tile | Shows | Source | Limit |
| --- | --- | --- | --- |
| Tokens · 7 days | One total, % read from cache, output tokens, total per agent | `core/tokens.py` `TokenLedger`: reads Claude Code and Codex transcripts incrementally, **per UTC day**, per agent, split into input / cache read / cache write / output | Page asks for 7 days only. No per-model or per-session split. Transcripts on this Mac go back to 2026-07-19 (about 10 weeks). |
| Agent Mail · 24 h | Questions sent, answered, Oracle nudges | `session_api.mail_stats` (one count query) and `OracleNudge` events | Closed messages are deleted after 7 days (`session_api._sweep`), so older mail can't be counted. |

## 1. Analytics page

- Opens from either tile (landing on its tab), and from a link on the Oracle
  page header. Back returns to the Oracle page.
- **Range:** 24 hours, 7 days, 30 days, All. Default 7 days. Remembered per
  viewer.
- **Filters:** agent (Claude Code, Codex), folder, session.
- Every chart has a table view for exact numbers.

## 2. Tokens tab

| Chart | What it answers |
| --- | --- |
| Tokens per day, stacked: input, cache read, cache write, output | How usage changes day to day, and what kind |
| Tokens by model | Which models use the most |
| Tokens by agent | Claude Code vs Codex |
| Top sessions and top folders | Where the tokens go |
| Cache read rate per day | How much input is served from cache |
| Headline numbers | Total, daily average, busiest day, output total |

**Backend changes:**

- `TokenLedger` also buckets by **model** (Claude: `message.model` on each
  assistant line, already read for the context readout; Codex: the model in
  the rollout's turn context) and by **transcript file**.
- Map transcript files to DuckTerm sessions (and so to folders) with the
  native session IDs already recorded from hooks. Transcripts with no
  DuckTerm session show as "Outside DuckTerm".
- New route `GET /analytics/tokens?days=N&by=day|model|agent|session|folder`.
  The existing `/control-tower` totals stay as they are.

**Cost:** not in v1. Prices change and differ by plan (a subscription vs the
API), so a dollar figure would often be wrong. Owner question 2.

## 3. Agent Mail tab

| Chart | What it answers |
| --- | --- |
| Messages per day: sent, answered, declined, expired | Volume and follow-through |
| Time to answer (median and slowest) per day | How responsive sessions are |
| Busiest senders and recipients, and top sender → recipient pairs | Who talks to whom |
| Open now: queued and accepted, oldest first | What's waiting |
| Owner broadcasts and Oracle nudges per day | How much the owner and Oracle have to step in |

**Backend changes:**

- A **daily rollup** table of counts per day, sender, recipient, kind and
  final status, plus answer-time totals. It is written before the 7-day
  sweep deletes a message, so history accumulates from the day this ships.
  It holds counts only, never message text.
- History before shipping is limited to the last 7 days. The page says so.
- New route `GET /analytics/mail?days=N`.

## 4. Charts without a new dependency

The web app has no chart library today. Draw the charts as inline SVG
components (bars, stacked bars, lines, a ranked list) themed with the
existing tokens for light and dark. Revisit only if a chart type the page
needs is hard to draw by hand.

## 5. Out of scope for v1

Dollar cost; alerts or budgets; exporting; tokens for Copilot (no usage in
its transcripts today); analytics for remote sessions' transcripts (they live
on the remote machine).

## 6. Open questions for the owner

All answered; see Owner decisions at the top.

## Delivery

The architect designs, the owner reviews a preview (visible UI change,
AGENTS.md), main-dev builds the ledger, rollup and routes, ui-dev builds the
page, release-dev ships.
