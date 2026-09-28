# Agent Mail analytics backend

`GET /analytics/mail?days=7` returns counts and response timing for this local
DuckTerm instance. It requires the owner's `X-Duckterm-Token`; session credentials
cannot access it. The Analytics page is a separate UI change.

`days` defaults to 7, accepts 1–36500 or `all`, and means UTC calendar days
including today. `days=1` means today, **not a rolling 24-hour window**. The
response identifies its timezone and schema `version: 1`. Days without activity
are omitted; a chart can fill them with zeroes within the requested range.

- `daily`: questions sent on send day; answered, declined, expired and cancelled
  on completion day; broadcasts on send day; Oracle nudges on event day.
  Broadcast counts represent recipient deliveries, not distinct folder actions.
- `cohorts`: sent-day question outcomes. These legitimately change as questions
  close, and must not be presented as completion-day activity.
- `duration_count`, `duration_sum_ms`, `mean_ms`, `slowest_ms`: answer timing for
  answered questions, on completion day. Mean and maximum are exact. Days with
  no answers have null timing summaries.
- `histogram`: seven counts with upper bounds in `histogram_upper_bounds_ms`:
  1m, 5m, 15m, 1h, 4h, 24h, unbounded. Bounds are exclusive. Median and p90
  (`median_approx_ms`, `p90_approx_ms`) interpolate within buckets and **must be
  labelled approximate**. The final bucket uses its observed maximum as upper
  bound. Bucket definitions are immutable for rollup v1.
- `top_pairs`, `top_senders`, `top_recipients`: top 20 by questions sent in range,
  with stable session IDs, not retained display names or message text.
- `open_now`: all current queued/accepted questions, regardless of range; counts
  plus the oldest 100 IDs, participants, statuses and send timestamps. No text.

`enabled_at` records when this installation first enabled the rollup. Previously
retained rows are included, but messages deleted before upgrading cannot be
recovered. Do not imply complete history before that date. Legacy closed rows
without a completion timestamp contribute sent/cohort counts only; expiry uses
the recorded deadline. Test sessions are excluded.

## Storage and retention contract

`mail_rollup_v1` retains only day, sender/recipient IDs, kind, series, status,
histogram bucket, counts, duration sums and maxima. It retains no message IDs,
message text, display names, credentials or individual durations.

A mail row is either live or rolled up, never both. Read-time analytics combine
live contributions with durable aggregates. The seven-day sweep contributes and
deletes in one SQLite savepoint inside its transaction. Startup uses this same
path for retained old rows; repeated sweeps or restarts cannot duplicate counts.
Owner session deletion also preserves counts. Test-data purges discard test data.
Oracle nudges use the same approach at the 30-day event sweep and session deletion.

Schema v6 prevents an older server from reopening the database and deleting rows
without preserving their counts. Upgrade all servers that share the database;
a pre-upgrade backup is needed to return to a v5-only binary. No new Python
packages or background services are required.

Verification covers before/after sweep equality, restart, v5 migration, injected
delete failure and retry, owner deletion, test exclusion, broadcasts, nudges,
UTC day attribution, histogram edges, exact mean/maximum, and HTTP authorization
and range validation. The page, token analytics, filters, and remote aggregation
are outside this backend change.
