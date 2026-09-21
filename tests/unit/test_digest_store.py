"""Digest history store + validation verdicts: items accumulate, duplicates
never insert twice (code guard, independent of the validator), next_actions
complete instead of vanishing, and a failed validator falls back to a merge
that can't lose data."""

from pathlib import Path

from duckterm.core import progress
from duckterm.persistence.digests import DigestStore


def test_merge_accumulates_and_dedupes(tmp_path: Path) -> None:
    store = DigestStore(tmp_path / "db.sqlite")
    r1 = store.merge(
        "S",
        [
            {"bucket": "deliverables", "text": "Login page shipped"},
            {"bucket": "next_actions", "text": "Add tests"},
        ],
        [],
        now=1000,
    )
    assert r1 == {"inserted": 2, "done": 0}

    # Same text again (different case/punctuation) must not duplicate; junk
    # buckets and empty text are dropped even if a validator accepted them.
    r2 = store.merge(
        "S",
        [
            {"bucket": "deliverables", "text": "login page SHIPPED!"},
            {"bucket": "bogus", "text": "x"},
            {"bucket": "learnings", "text": "   "},
            {"bucket": "learnings", "text": "sqlite is enough"},
        ],
        [],
        now=2000,
    )
    assert r2 == {"inserted": 1, "done": 0}
    items = store.items("S")
    assert len(items) == 3
    assert {i["bucket"] for i in items} == {"deliverables", "next_actions", "learnings"}


def test_next_actions_complete_instead_of_vanishing(tmp_path: Path) -> None:
    store = DigestStore(tmp_path / "db.sqlite")
    store.merge("S", [{"bucket": "next_actions", "text": "Wire login"}], [], now=1000)
    action_id = store.items("S")[0]["id"]

    result = store.merge(
        "S",
        [{"bucket": "deliverables", "text": "Login wired"}],
        [action_id, "nonexistent"],
        now=2000,
    )
    assert result == {"inserted": 1, "done": 1}
    by_bucket = {i["bucket"]: i for i in store.items("S")}
    assert by_bucket["next_actions"]["status"] == "done"  # kept, flipped
    assert by_bucket["next_actions"]["updated_at"] == 2000
    assert by_bucket["deliverables"]["status"] == "active"


def test_done_only_applies_to_own_session_next_actions(tmp_path: Path) -> None:
    store = DigestStore(tmp_path / "db.sqlite")
    store.merge("A", [{"bucket": "learnings", "text": "keep it simple"}], [], now=1)
    learning_id = store.items("A")[0]["id"]
    # A learning can't be marked done, and another session can't touch it.
    assert store.merge("A", [], [learning_id], now=2)["done"] == 0
    assert store.merge("B", [], [learning_id], now=2)["done"] == 0


def test_delete_session_clears_items(tmp_path: Path) -> None:
    store = DigestStore(tmp_path / "db.sqlite")
    store.merge("S", [{"bucket": "learnings", "text": "x y z"}], [], now=1)
    store.delete_session("S")
    assert store.items("S") == []


def test_parse_verdicts_and_fallback() -> None:
    good = """Here: {"accept": [{"bucket": "learnings", "text": "a"}],
      "reject": [{"text": "vague", "reason": "not concrete"}],
      "done_next_action_ids": ["id1", 2]}"""
    verdicts = progress.parse_verdicts(good)
    assert verdicts is not None
    assert verdicts["accept"] == [{"bucket": "learnings", "text": "a"}]
    assert verdicts["done_next_action_ids"] == ["id1", "2"]

    assert progress.parse_verdicts("no json here") is None
    assert progress.parse_verdicts('{"accept": "not-a-list"}') is None

    digest = {
        "summary": "s",
        "deliverables": ["d1"],
        "learnings": [],
        "user_learnings": [],
        "next_actions": ["n1"],
    }
    fb = progress.fallback_verdicts(digest)
    assert {"bucket": "deliverables", "text": "d1"} in fb["accept"]
    assert {"bucket": "next_actions", "text": "n1"} in fb["accept"]
    assert fb["done_next_action_ids"] == []


def test_validate_prompt_includes_candidates_and_existing(tmp_path: Path) -> None:
    store = DigestStore(tmp_path / "db.sqlite")
    store.merge("S", [{"bucket": "next_actions", "text": "old action"}], [], now=1)
    digest = {
        "summary": "s",
        "deliverables": ["new thing"],
        "learnings": [],
        "user_learnings": [],
        "next_actions": [],
    }
    prompt = progress.validate_prompt(digest, store.items("S"))
    assert "new thing" in prompt
    assert "old action" in prompt
    assert "next_actions" in prompt
