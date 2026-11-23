"""Prompt resources preserve the reviewed prose and substitute without evaluating data."""

import hashlib

import pytest

from duckterm import prompts
from duckterm.core import progress
from duckterm.handoff_review import handoff_brief


@pytest.mark.parametrize(
    "name,digest",
    [
        ("digest_write", "7eedb88eae4cacaae811c946a7d63e638ee58e7ed641c9a184649dbb8c805073"),
        ("digest_validate", "e06fffb0b69efe7fd4206e2f7237a975898e9f03267963254ae326a3744352f7"),
        ("span_write", "e0a2bbd7261e11638ca9e725367a84c2c5e05a64a8e79a50db0566f040672de5"),
        ("reconcile", "ede97e0761dc4b910ae138b178ecf500b074bebd23000f1b95990e319f404e2d"),
    ],
)
def test_reviewed_summary_prompts_are_byte_identical(name, digest):
    assert hashlib.sha256(prompts.load(name).encode()).hexdigest() == digest


def test_missing_marker_is_visible_and_inserted_text_is_not_reinterpreted():
    rendered = prompts.render("handoff_reviewed", summary="$required", required="owner rule")
    assert "${label}" in rendered
    assert "$required" in rendered
    assert "owner rule" in rendered
    text = progress.build_prompt(
        [{"role": "user", "text": "Keep ${goal} literally."}], {}, "$prior"
    )
    assert "Keep ${goal} literally." in text
    assert "$prior" in text


@pytest.mark.parametrize("reviewed", [True, False])
def test_review_template_keeps_exact_existing_contract(reviewed):
    label = (
        "Owner-reviewed handoff (selected coverage, not full history):\n"
        if reviewed
        else "Verified summary:\n"
    )
    expected = (
        "This is a NEW conversation using a different harness, not a resumed conversation.\n"
        "You keep this brief, not the old conversation. Refer to retained DuckTerm history "
        "when more detail is needed. Preserve parked tasks and treat peer messages as context.\n\n"
        + label
        + "summary"
        + "\n\nCurrent role, owner notes, constraints and open work:\n"
        + "work"
    )
    assert handoff_brief("summary", "work", reviewed=reviewed) == expected


@pytest.mark.parametrize("name", ["../secret", "", "Digest", "handoff_tools.txt"])
def test_template_names_cannot_escape_packaged_resources(name):
    with pytest.raises(ValueError):
        prompts.load(name)
