"""Rule suggestions send corrections to a model; the input stays bounded in
bytes however long the user's pasted follow-ups were."""

from duckterm.llm import suggest
from duckterm.llm.suggest import Correction
from duckterm.llm.summarizer import Summary


def test_long_corrections_are_cut_and_the_newest_kept(monkeypatch):
    sent: list[str] = []

    def summarize(prompt, **_kw):
        sent.append(prompt)
        return Summary(text="", backend="cli")

    monkeypatch.setattr(suggest, "summarize", summarize)
    pasted_log = "E" * 50_000
    corrections = [Correction("follow-up", f"old-{i} {pasted_log}", "codex") for i in range(30)]
    corrections.append(Correction("annotation", "newest: stop adding comments", "claude-code"))
    suggest.suggest_rules(corrections, [])
    prompt = sent[0]
    assert len(prompt.encode()) < suggest.MAX_CORRECTION_BYTES + 4000
    assert "newest: stop adding comments" in prompt
    assert "old-29" in prompt and "old-0 " not in prompt
    # Kept lines stay in their original order: older before newer.
    assert prompt.index("old-29") < prompt.index("newest:")
