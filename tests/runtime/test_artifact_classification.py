"""Cold-start classifications stay inferred and never overwrite declared kinds."""

import pytest
from test_artifacts import app

from duckterm.persistence import artifacts

__all__ = ["app"]


def test_kind_required_and_legacy_backfill_is_explicit_idempotent(app, tmp_path):
    server, _, _ = app
    store = server.history.artifacts
    file = tmp_path / "spec.md"
    file.write_text("# Original document")
    request = artifacts.registration(file, "Spec — memory")
    with pytest.raises(artifacts.ArtifactError, match="kind is required"):
        store.register("a", request)
    saved = store.register("a", {**request, "kind": "spec"})
    assert store.classification_plan() == []
    # A real legacy row has no metadata. Simulate it without changing its body.
    with store.conn:
        store.conn.execute("DELETE FROM artifact_metadata WHERE id=?", (saved["id"],))
    plan = store.classification_plan()
    assert plan == [
        {"id": saved["id"], "session_key": "a", "kind": "spec", "kind_source": "inferred"}
    ]
    assert store.classify_existing() == 1
    assert store.classify_existing() == 0
    assert (
        store.conn.execute("SELECT content FROM artifacts WHERE id=?", (saved["id"],)).fetchone()[0]
        == b"# Original document"
    )
    row = store.list("a")[0]
    assert row["kind"] == "spec" and row["kind_source"] == "inferred"
    store.register("a", {**request, "kind": "research"})
    assert store.classify_existing() == 0
    assert store.list("a")[0]["kind_source"] == "declared"


@pytest.mark.parametrize(
    "media,title,expected",
    [
        ("image/png", "Spec screenshot", "evidence"),
        ("application/octet-stream", "Unlabelled payload", "binary"),
        ("text/markdown", "Spec — ongoing work", "spec"),
        ("text/markdown", "Notes", "unclassified"),
    ],
)
def test_deterministic_inference_keeps_ambiguous_text_unclassified(media, title, expected):
    assert artifacts.inferred_kind({"media_type": media, "title": title}) == expected
