import pytest

from duckterm.review import status

OK = {"failed": 0, "pending": 0}
PENDING = {"failed": 0, "pending": 1}
FAILED = {"failed": 2, "pending": 0}


def pr(state: str = "open", draft: bool = False) -> dict:  # type: ignore[type-arg]
    return {"state": state, "draft": draft, "number": 7}


@pytest.mark.parametrize(
    ("pull", "facts", "expected", "needs_you"),
    [
        (None, {"pushed": False, "conflict": None, "checks": None}, "unpushed", False),
        (None, {"pushed": True, "conflict": False, "checks": None}, "pushed", False),
        (
            pr(draft=True),
            {"pushed": True, "conflict": True, "checks": {"failed": 1}},
            "draft",
            False,
        ),
        (pr(), {"pushed": True, "conflict": True, "checks": {"failed": 0}}, "conflict", True),
        (
            pr(),
            {"pushed": True, "conflict": False, "checks": {"failed": 2, "pending": 0}},
            "ci_failing",
            True,
        ),
        (pr(), {"pushed": True, "conflict": False, "checks": None}, "open", True),
        (
            pr(),
            {
                "pushed": True,
                "conflict": False,
                "checks": {"failed": 0, "pending": 0},
                "review": "changes_requested",
            },
            "changes_requested",
            False,
        ),
        (
            pr(),
            {
                "pushed": True,
                "conflict": False,
                "checks": {"failed": 0, "pending": 0},
                "review": "approved",
            },
            "ready",
            True,
        ),
        (
            pr(),
            {
                "pushed": True,
                "conflict": False,
                "checks": {"failed": 0, "pending": 1},
                "review": "approved",
            },
            "open",
            True,
        ),
        (
            pr(),
            {
                "pushed": True,
                "conflict": None,
                "checks": {"failed": 0, "pending": 0},
                "review": "approved",
            },
            "open",
            True,
        ),
        (pr("merged"), {"pushed": True, "conflict": None, "checks": None}, "merged", False),
        (pr("closed"), {"pushed": True, "conflict": None, "checks": None}, "closed", False),
    ],
)
def test_status_precedence(pull, facts, expected, needs_you) -> None:  # type: ignore[no-untyped-def]
    verdict = status.derive(pull, **facts)
    assert verdict["status"] == expected
    assert verdict["needs_you"] is needs_you
    assert verdict["label"] == status.LABELS[expected]


def test_merged_label_names_the_release() -> None:
    verdict = status.derive(
        pr("merged"), pushed=True, conflict=None, checks=None, version="v0.4.173"
    )
    assert verdict["label"] == "Merged · v0.4.173"
