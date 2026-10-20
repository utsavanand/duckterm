"""Retained text references verify bytes and never redirect to a new source version."""

import json

import pytest

from duckterm import memory_sources, memory_versions
from duckterm.core.session_api import APIError
from duckterm.persistence.saved_state import fingerprint


def source(text):
    records = [{"id": "0", "role": "artifact", "text": text}]
    return {
        "id": "a" * 32,
        "version": fingerprint(records),
        "kind": "artifact",
        "title": "Design",
        "artifact_id": "artifact-1",
        "records": records,
    }


def test_exact_old_version_survives_new_version_without_mixing_records(tmp_path, monkeypatch):
    monkeypatch.setenv("DUCKTERM_HOME", str(tmp_path))
    old = source("Use GCP for the service")
    first = memory_versions.retain("test-agent", old, "Tests")
    repeat = memory_versions.retain("test-agent", old, "Tests")
    new = memory_versions.retain("test-agent", source("Use Railway"), "Tests")
    assert first == repeat and first["text_snapshot"] != new["text_snapshot"]
    assert memory_versions.read("test-agent", first, "Tests")["records"] == old["records"]
    with pytest.raises(APIError, match="scope"):
        memory_versions.read("test-agent", first, "Other")
    with pytest.raises(APIError, match="unavailable"):
        memory_versions.read("another-session", first, "Tests")


@pytest.mark.parametrize("change", ["corruption", "symlink", "identity"])
def test_invalid_snapshot_is_never_served(tmp_path, monkeypatch, change):
    monkeypatch.setenv("DUCKTERM_HOME", str(tmp_path))
    reference = memory_versions.retain("test-agent", source("Original"), "Tests")
    path = memory_sources.directory("test-agent") / (
        "record-" + reference["text_snapshot"] + ".json"
    )
    if change == "corruption":
        path.write_text(json.dumps(source("Rewritten")))
    elif change == "symlink":
        real = path.with_suffix(".copy")
        path.rename(real)
        path.symlink_to(real)
    else:
        reference = {**reference, "version": "f" * 64}
    with pytest.raises(APIError):
        memory_versions.read("test-agent", reference, "Tests")
