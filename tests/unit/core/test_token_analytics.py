import json
from datetime import UTC, datetime
from pathlib import Path

from duckterm.core.tokens import TokenLedger

NOW = datetime(2026, 9, 28, 12, tzinfo=UTC).timestamp()


def write(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))


def claude(model: str | None, ident: str, stamp: str = "2026-09-28T10:00:00Z") -> dict:
    return dict(
        type="assistant",
        timestamp=stamp,
        sessionId="native-claude",
        message=dict(id=ident, model=model, usage=dict(input_tokens=12, output_tokens=3)),
    )


def codex(total: int) -> dict:
    return dict(
        type="event_msg",
        timestamp="2026-09-28T10:00:00Z",
        payload=dict(
            type="token_count",
            info=dict(
                total_token_usage=dict(input_tokens=total, cached_input_tokens=0, output_tokens=0)
            ),
        ),
    )


def test_exact_models_remain_separate_including_mid_session_switch(tmp_path: Path) -> None:
    claude_root, codex_root = tmp_path / "claude", tmp_path / "codex"
    first = claude("claude-opus-4-1-20250805", "first")
    synthetic = claude("<synthetic>", "empty")
    synthetic["message"]["usage"] = {}
    write(
        claude_root / "proj/native-claude.jsonl",
        [first, first, claude("claude-sonnet-4-20250514", "second"), claude(None, "third")],
    )
    path = codex_root / "rollout-test.jsonl"
    write(
        path,
        [
            dict(type="session_meta", payload=dict(id="native-codex")),
            codex(10),
            dict(type="turn_context", payload=dict(model="gpt-6-astra")),
            codex(40),
        ],
    )
    ledger = TokenLedger(claude_root, codex_root)
    result = ledger.analytics(7, NOW, [])
    models = {r["model"]: r for r in result["rows"] if r["agent"] == "claude-code"}
    assert set(models) == {"claude-opus-4-1-20250805", "claude-sonnet-4-20250514", None}
    assert models["claude-opus-4-1-20250805"]["input"] == 12  # duplicate block excluded
    codex_rows = {r["model"]: r for r in result["rows"] if r["agent"] == "codex"}
    assert codex_rows[None]["input"] == 10  # no retroactive attribution
    assert codex_rows["gpt-6-astra"]["input"] == 30
    with path.open("a") as fh:
        fh.write(json.dumps(dict(type="turn_context", payload=dict(model="gpt-6-sol"))) + "\n")
        fh.write(json.dumps(codex(75)) + "\n")
    result = ledger.analytics(7, NOW, [])
    assert next(r for r in result["rows"] if r["model"] == "gpt-6-sol")["input"] == 35
    assert ledger.totals(7, NOW)["codex"]["input"] == 75


def test_identity_filters_utc_history_and_tests(tmp_path: Path) -> None:
    root = tmp_path / "claude"
    write(root / "a.jsonl", [claude("exact-id", "old", "2026-09-27T23:30:00-07:00")])
    identity = dict(
        key="owner-session",
        name="Builder",
        folder="Project/Client",
        runtime="claude-code",
        native_ids=["native-claude"],
        test=False,
    )
    ledger = TokenLedger(root, tmp_path / "codex")
    result = ledger.analytics(1, NOW, [identity], session="owner-session", folder="Project/Client")
    assert result["rows"][0]["day"] == "2026-09-28"
    assert result["rows"][0]["session_name"] == "Builder"
    assert ledger.analytics(1, NOW, [identity], agent="codex")["rows"] == []
    assert ledger.analytics(1, NOW, [{**identity, "test": True}])["rows"] == []
    ambiguous = ledger.analytics(1, NOW, [identity, {**identity, "key": "another"}])
    assert ambiguous["rows"][0]["session"] == "outside"
    assert ambiguous["rows"][0]["model"] == "exact-id"
    # Scans all retained history even after the seven-day tower scan.
    write(root / "old.jsonl", [claude("older-model", "historic", "2026-07-19T10:00:00Z")])
    ledger.totals(7, NOW)
    assert ledger.analytics(None, NOW, [])["earliest_day"] == "2026-07-19"


def test_rewritten_and_partial_transcripts_do_not_double_count(tmp_path: Path) -> None:
    path = tmp_path / "claude/a.jsonl"
    write(path, [claude("first-exact-model", "one")])
    ledger = TokenLedger(path.parent, tmp_path / "codex")
    assert len(ledger.analytics(None, NOW, [])["rows"]) == 1
    path.write_text('{"type":')
    assert ledger.analytics(None, NOW, [])["rows"] == []
    write(path, [claude("new-exact-model", "two")])
    assert [r["model"] for r in ledger.analytics(None, NOW, [])["rows"]] == ["new-exact-model"]
