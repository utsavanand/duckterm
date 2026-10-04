"""Counts-only UTC mail history. Live rows transfer atomically at deletion.

No message IDs, text, names, credentials, or individual durations are retained.
The v1 histogram boundaries are immutable; a new definition needs a new version.
"""

import sqlite3
import time
from collections import defaultdict
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any

RollupKey = tuple[str, str, str, str, str, str, int]
Contribution = tuple[str, str, str, str, str, str, int, int, int, int]

DAY = 86_400_000
BOUNDS = (60_000, 300_000, 900_000, 3_600_000, 14_400_000, DAY)
CLOSED = ("answered", "declined", "expired", "cancelled")
MAIL_COLUMNS = "sender, recipient, kind, status, created_at, expires_at, answered_at"
REAL_MAIL = (
    "NOT EXISTS (SELECT 1 FROM sessions s WHERE s.test=1 "
    "AND s.session_key IN (q.sender,q.recipient))"
)
REAL_NUDGE = "NOT EXISTS (SELECT 1 FROM sessions s WHERE s.test=1 AND s.session_key=e.session_key)"
SCHEMA = """
CREATE TABLE IF NOT EXISTS mail_rollup_v1 (
 day TEXT NOT NULL, sender TEXT NOT NULL, recipient TEXT NOT NULL,
 kind TEXT NOT NULL, series TEXT NOT NULL, status TEXT NOT NULL,
 bucket INTEGER NOT NULL, count INTEGER NOT NULL,
 duration_sum_ms INTEGER NOT NULL, duration_max_ms INTEGER NOT NULL,
 PRIMARY KEY(day,sender,recipient,kind,series,status,bucket)
);
CREATE TABLE IF NOT EXISTS mail_analytics_meta (
 key TEXT PRIMARY KEY, value INTEGER NOT NULL
);
"""


def initialize(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.execute(
        "INSERT OR IGNORE INTO mail_analytics_meta VALUES ('enabled_at',?)",
        (int(time.time() * 1000),),
    )


def day(ts: int) -> str:
    return datetime.fromtimestamp(ts / 1000, UTC).date().isoformat()


@contextmanager
def atomic(conn: sqlite3.Connection) -> Iterator[None]:
    # A savepoint composes with the caller's transaction without committing it.
    conn.execute("SAVEPOINT mail_analytics")
    try:
        yield
    except BaseException:
        conn.execute("ROLLBACK TO mail_analytics")
        conn.execute("RELEASE mail_analytics")
        raise
    else:
        conn.execute("RELEASE mail_analytics")


def contributions(row: sqlite3.Row) -> Iterator[Contribution]:
    sent = day(row["created_at"])
    base = (row["sender"], row["recipient"], row["kind"])
    yield (sent, *base, "sent", "", -1, 1, 0, 0)
    status = row["status"]
    if status not in CLOSED:
        return
    ended = row["answered_at"]
    if ended is None and status == "expired":
        ended = row["expires_at"]
    # Legacy rows may lack completion stamps; never invent a completion day.
    yield (sent, *base, "cohort", status, -1, 1, 0, 0)
    if ended is None or ended <= 0:
        return
    completed = day(ended)
    yield (completed, *base, "completed", status, -1, 1, 0, 0)
    if status == "answered" and row["kind"] == "question":
        duration = max(0, ended - row["created_at"])
        bucket = next((i for i, bound in enumerate(BOUNDS) if duration < bound), len(BOUNDS))
        yield (completed, *base, "duration", "", bucket, 1, duration, duration)


def _add(conn: sqlite3.Connection, values: Iterable[Contribution]) -> None:
    conn.executemany(
        """INSERT INTO mail_rollup_v1 VALUES (?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(day,sender,recipient,kind,series,status,bucket) DO UPDATE SET
        count=count+excluded.count,
        duration_sum_ms=duration_sum_ms+excluded.duration_sum_ms,
        duration_max_ms=MAX(duration_max_ms,excluded.duration_max_ms)""",
        values,
    )


def retire_mail(conn: sqlite3.Connection, where: str, params: tuple[object, ...] = ()) -> None:
    """Internal SQL predicate only. Transfer and deletion succeed or fail together."""
    with atomic(conn):
        rows = conn.execute(
            f"SELECT {MAIL_COLUMNS} FROM session_questions q " f"WHERE ({where}) AND {REAL_MAIL}",
            params,
        ).fetchall()
        _add(conn, (value for row in rows for value in contributions(row)))
        conn.execute(f"DELETE FROM session_questions AS q WHERE {where}", params)


def retire_events(conn: sqlite3.Connection, where: str, params: tuple[object, ...] = ()) -> None:
    with atomic(conn):
        rows = conn.execute(
            "SELECT ts, session_key FROM events e WHERE event_type='OracleNudge' "
            f"AND ({where}) AND {REAL_NUDGE}",
            params,
        ).fetchall()
        _add(
            conn,
            ((day(r["ts"]), "", r["session_key"], "nudge", "nudge", "", -1, 1, 0, 0) for r in rows),
        )
        conn.execute(f"DELETE FROM events AS e WHERE {where}", params)


def _percentile(histogram: list[int], fraction: float, maximum: int) -> float | None:
    count = sum(histogram)
    if not count:
        return None
    rank = count * fraction
    seen = 0
    for i, size in enumerate(histogram):
        if size and seen + size >= rank:
            low = BOUNDS[i - 1] if i else 0
            high = BOUNDS[i] if i < len(BOUNDS) else max(maximum, low)
            return low + (high - low) * (rank - seen) / size
        seen += size
    return None


def snapshot(
    conn: sqlite3.Connection,
    days: int | None = 7,
    *,
    now: int | None = None,
    sessions: set[str] | None = None,
) -> dict[str, Any]:
    """Calendar UTC days including today. None means all retained history."""
    now = int(time.time() * 1000) if now is None else now
    start = day(now - (days - 1) * DAY) if days else "0000-01-01"
    merged: dict[RollupKey, list[int]] = {}
    with atomic(conn):
        for row in conn.execute("SELECT * FROM mail_rollup_v1 WHERE day>=?", (start,)):
            key: RollupKey = (
                row["day"],
                row["sender"],
                row["recipient"],
                row["kind"],
                row["series"],
                row["status"],
                row["bucket"],
            )
            merged[key] = [row["count"], row["duration_sum_ms"], row["duration_max_ms"]]
        for row in conn.execute(
            f"SELECT {MAIL_COLUMNS} FROM session_questions q WHERE {REAL_MAIL}"
        ):
            for value in contributions(row):
                if value[0] < start:
                    continue
                totals = merged.setdefault(value[:7], [0, 0, 0])
                totals[0] += value[7]
                totals[1] += value[8]
                totals[2] = max(totals[2], value[9])
        for row in conn.execute(
            "SELECT ts,session_key FROM events e WHERE event_type='OracleNudge' "
            f"AND {REAL_NUDGE}"
        ):
            when = day(row["ts"])
            if when >= start:
                key = (when, "", row["session_key"], "nudge", "nudge", "", -1)
                merged.setdefault(key, [0, 0, 0])[0] += 1
        open_rows = [
            dict(r)
            for r in conn.execute(
                "SELECT id,sender,recipient,status,created_at FROM session_questions q "
                f"WHERE kind='question' AND status IN ('queued','accepted') AND {REAL_MAIL} "
                "ORDER BY created_at,id"
            )
        ]
        enabled = conn.execute(
            "SELECT value FROM mail_analytics_meta WHERE key='enabled_at'"
        ).fetchone()[0]
    if sessions is not None:
        merged = {
            key: value for key, value in merged.items() if key[1] in sessions or key[2] in sessions
        }
        open_rows = [
            row for row in open_rows if row["sender"] in sessions or row["recipient"] in sessions
        ]
    daily: dict[str, dict[str, Any]] = {}
    cohorts: dict[str, dict[str, int]] = {}
    pairs: dict[tuple[str, str], int] = defaultdict(int)
    for (when, sender, recipient, kind, series, status, bucket), (count, total, maximum) in sorted(
        merged.items()
    ):
        entry = daily.setdefault(
            when,
            dict(
                day=when,
                sent=0,
                answered=0,
                declined=0,
                expired=0,
                cancelled=0,
                broadcasts=0,
                nudges=0,
                duration_count=0,
                duration_sum_ms=0,
                slowest_ms=0,
                histogram=[0] * 7,
            ),
        )
        if series == "nudge":
            entry["nudges"] += count
        elif kind == "broadcast":
            if series == "sent":
                entry["broadcasts"] += count
        elif series == "sent":
            entry["sent"] += count
            pairs[sender, recipient] += count
            cohort = cohorts.setdefault(
                when, dict(sent=0, answered=0, declined=0, expired=0, cancelled=0)
            )
            cohort["sent"] += count
        elif series == "cohort":
            cohort = cohorts.setdefault(
                when, dict(sent=0, answered=0, declined=0, expired=0, cancelled=0)
            )
            cohort[status] += count
        elif series == "completed":
            entry[status] += count
        elif series == "duration":
            entry["duration_count"] += count
            entry["duration_sum_ms"] += total
            entry["slowest_ms"] = max(entry["slowest_ms"], maximum)
            entry["histogram"][bucket] += count
    for entry in daily.values():
        entry["mean_ms"] = (
            entry["duration_sum_ms"] / entry["duration_count"] if entry["duration_count"] else None
        )
        entry["median_approx_ms"] = _percentile(entry["histogram"], 0.5, entry["slowest_ms"])
        entry["p90_approx_ms"] = _percentile(entry["histogram"], 0.9, entry["slowest_ms"])
        if not entry["duration_count"]:
            entry["slowest_ms"] = None
    senders: dict[str, int] = defaultdict(int)
    recipients: dict[str, int] = defaultdict(int)
    for (sender, recipient), count in pairs.items():
        senders[sender] += count
        recipients[recipient] += count
    return dict(
        version=1,
        timezone="UTC",
        days=days,
        enabled_at=enabled,
        history_note=(
            "Earlier deleted messages are unavailable; " "retained pre-upgrade rows are included."
        ),
        histogram_upper_bounds_ms=[*BOUNDS, None],
        daily=list(daily.values()),
        cohorts=[dict(day=when, **counts) for when, counts in cohorts.items()],
        top_senders=[
            dict(session=key, sent=count)
            for key, count in sorted(senders.items(), key=lambda x: (-x[1], x[0]))[:20]
        ],
        top_recipients=[
            dict(session=key, received=count)
            for key, count in sorted(recipients.items(), key=lambda x: (-x[1], x[0]))[:20]
        ],
        top_pairs=[
            dict(sender=s, recipient=r, sent=count)
            for (s, r), count in sorted(pairs.items(), key=lambda x: (-x[1], x[0]))[:20]
        ],
        open_now=dict(
            queued=sum(r["status"] == "queued" for r in open_rows),
            accepted=sum(r["status"] == "accepted" for r in open_rows),
            oldest=open_rows[:100],
        ),
    )
