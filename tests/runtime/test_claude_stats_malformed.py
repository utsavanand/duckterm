"""A malformed record must not break the entire dashboard agent list."""

import json

import pytest
from test_memory import memory_rig, transcript
from test_session_api import dispatch

from duckterm.runtimes.claude_code import transcript_stats

__all__ = ["memory_rig"]

BAD_RECORDS = [
    [],
    None,
    42,
    "text",
    {"type": "assistant", "message": [1]},
    {"type": "assistant", "message": {"usage": [1]}},
    *(
        {"type": "assistant", "message": {"usage": {"input_tokens": value}}}
        for value in ("not-a-number", [1], {"n": 1}, -1, float("inf"))
    ),
]


@pytest.mark.parametrize("bad", BAD_RECORDS)
def test_malformed_record_keeps_prior_valid_stats(tmp_path, bad):
    path = tmp_path / "transcript.jsonl"
    good = {
        "type": "assistant",
        "message": {"model": "fixture-model", "usage": {"input_tokens": 42}},
    }
    path.write_text(json.dumps(good) + "\n" + json.dumps(bad) + "\n")
    assert transcript_stats(path) == {"context_tokens": 42, "model": "fixture-model"}
    path.write_text(json.dumps(bad) + "\n")
    assert transcript_stats(path) == {"context_tokens": None, "model": None}


def test_sessions_endpoint_survives_malformed_transcript(memory_rig):
    server, _, _, tmp = memory_rig
    path = transcript(tmp, "agent-claude", "synthetic")
    path.write_text("[]\n")
    status, body = dispatch(server, "GET", "/sessions", {"x-duckterm-token": server.token})
    assert status == 200
    assert {row["session_key"] for row in body["sessions"]} == {"agent", "peer"}
    assert all(row["context_tokens"] is None for row in body["sessions"])
