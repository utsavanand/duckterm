import pytest
from test_memory_reconcile import clean, seeded, store

__all__ = ["store"]


@pytest.mark.parametrize(
    "op",
    [
        {"op": "done", "id": [], "by": "s1"},
        {"op": "merge", "ids": [["o1"], "o2"], "kind": "task", "text": "merged"},
        {"op": "merge", "ids": 42, "kind": "task", "text": "merged"},
        {"op": "merge", "ids": ["o1", "o2"], "kind": [], "text": "merged"},
    ],
)
def test_malformed_cleanup_is_optional_and_preserves_items(store, op):
    before = seeded(store, 160)
    result, prompts, after = clean(store, dict.fromkeys(["intent", "rules", "facts"], [op]))
    assert [i["id"] for i in after["items"]] == [i["id"] for i in before["items"]]
