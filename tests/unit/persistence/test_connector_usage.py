"""Connector usage read from harness tool events, against a real store."""

import json

import pytest

from duckterm.persistence.history import HistoryStore


@pytest.fixture
def store(tmp_path):
    result = HistoryStore(tmp_path / "db.sqlite")
    yield result
    result.close()


def record(store: HistoryStore, tool: str, ts: int, event_type: str = "PreToolUse") -> None:
    store._conn.execute(
        "INSERT INTO events (session_key, event_type, ts, payload_json) VALUES (?, ?, ?, ?)",
        ("s1", event_type, ts, json.dumps({"event_type": event_type, "tool_name": tool})),
    )
    store._conn.commit()


def test_counts_calls_per_connector_and_keeps_the_newest_timestamp(store: HistoryStore) -> None:
    record(store, "mcp__github__list_issues", 1000)
    record(store, "mcp__github__create_pr", 3000)
    record(store, "mcp__porkbun__dns_list", 2000)
    assert store.connector_last_used() == {"github": (3000, 2), "porkbun": (2000, 1)}


def test_ignores_tools_that_are_not_mcp_calls(store: HistoryStore) -> None:
    """Bash and Read share the table; only mcp__ names identify a connector."""
    record(store, "Bash", 1000)
    record(store, "Read", 2000)
    record(store, "mcp__railway__deploy", 3000)
    assert store.connector_last_used() == {"railway": (3000, 1)}


def test_ignores_malformed_and_non_call_events(store: HistoryStore) -> None:
    record(store, "mcp__", 1000)  # no server segment
    record(store, "mcp____orphan", 1100)  # empty server segment
    record(store, "mcp__github__list_issues", 1200, event_type="PostToolUse")  # not a call
    assert store.connector_last_used() == {}


def test_reports_nothing_when_no_harness_has_reported_a_call(store: HistoryStore) -> None:
    """Absence must read as "no recorded use" upstream, never as "unused"."""
    assert store.connector_last_used() == {}
