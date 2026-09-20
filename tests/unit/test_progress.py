"""Progress digest: prompt assembly and strict-but-tolerant parsing."""

import json

from duckterm.core import progress


def test_parse_extracts_json_from_fenced_reply() -> None:
    body = (
        '{"summary": "Built the login flow.", "deliverables": ["login page"],'
        ' "learnings": ["bcrypt only"], "next_actions": ["add tests"]}'
    )
    reply = f"Here you go:\n```json\n{body}\n```"
    digest = progress.parse(reply)
    assert digest is not None
    assert digest["summary"] == "Built the login flow."
    assert digest["deliverables"] == ["login page"]
    assert digest["next_actions"] == ["add tests"]


def test_parse_rejects_garbage_and_empty() -> None:
    assert progress.parse("I could not produce a digest.") is None
    assert progress.parse('{"deliverables": [], "learnings": [], "next_actions": []}') is None
    assert progress.parse("{broken json") is None


def test_parse_caps_items_and_drops_unknown_keys() -> None:
    reply = json.dumps(
        {
            "summary": "s",
            "deliverables": [f"item {i}" for i in range(20)],
            "learnings": ["  padded  ", ""],
            "next_actions": "not-a-list",
            "extra": ["dropped"],
        }
    )
    digest = progress.parse(reply)
    assert digest is not None
    assert len(digest["deliverables"]) == 8  # capped
    assert digest["learnings"] == ["padded"]  # stripped, empties dropped
    assert digest["next_actions"] == []  # wrong type -> empty, not crash
    assert "extra" not in digest


def test_build_prompt_carries_prior_and_goal() -> None:
    prompt = progress.build_prompt(
        [{"role": "user", "text": "fix the bug"}],
        {"summary": "old", "deliverables": ["a fix"], "learnings": [], "next_actions": []},
        goal="ship v1",
    )
    assert '"a fix"' in prompt  # prior digest rides along
    assert "ship v1" in prompt
    assert "user: fix the bug" in prompt
