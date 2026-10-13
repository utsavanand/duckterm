"""Prompt resources preserve the reviewed prose and substitute without evaluating data."""

import hashlib

import pytest

from duckterm import prompts
from duckterm.core import progress
from duckterm.handoff_review import handoff_brief


@pytest.mark.parametrize(
    "name,digest",
    [
        ("digest_write", "62f5b3ce360f57d118861c7abd9c4acca467fbc6cc4253ea5c879b665b383b79"),
        ("digest_validate", "3dc1411f93bc9f8b60aa9da62012620e149cb57fdd17465c48de45bf50a3d32f"),
    ],
)
def test_extracted_digest_prose_is_byte_identical(name, digest):
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
