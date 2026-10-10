"""Independent diagnostics checks for missing inputs and malformed settings."""

import json

import pytest
from tests.runtime import test_resume_diagnostics as fixtures

from duckterm import resume_diagnostics

history = fixtures.history
seed = fixtures.seed


def test_missing_directory_is_not_reported_as_unsupported_lookup(history):
    seed(history, "qa-missing-cwd", "", "synthetic-id")
    history._conn.execute("UPDATE sessions SET cwd=NULL,worktree_path=NULL")
    history._conn.commit()
    text = resume_diagnostics.render(resume_diagnostics.snapshot(history, None))["text"]
    assert "directory source: missing" in text
    assert "expected transcript exists: not checked (no project directory)" in text


@pytest.mark.parametrize("entries", [[None], [{"hooks": None}], [{"hooks": "PRIVATE-SECRET"}]])
def test_malformed_hook_entries_never_export_configuration(history, tmp_path, entries):
    settings = tmp_path / ".claude" / "settings.json"
    settings.parent.mkdir()
    settings.write_text(json.dumps({"hooks": {"Stop": entries}}))
    assert resume_diagnostics.hook_status("claude-code", None) == "unknown"
