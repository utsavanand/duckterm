"""Development flags: off in every edition, on per instance through flags.json,
and every flag has a row in docs/feature-flags.md."""

import json
from pathlib import Path

import pytest

from duckterm import editions

DOC = Path(__file__).resolve().parents[2] / "docs" / "feature-flags.md"


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("DUCKTERM_HOME", str(tmp_path))
    monkeypatch.delenv("DUCKTERM_EDITION", raising=False)
    return tmp_path


def test_flags_are_off_in_every_edition():
    for edition in (editions.LITE, editions.CODE, editions.FULL):
        assert not edition.features & editions.DEVELOPMENT


def test_flags_file_adds_to_the_edition_and_a_retired_name_is_ignored(home, monkeypatch):
    (home / "flags.json").write_text(json.dumps({"enabled": ["skill_kits", "shipped_long_ago"]}))
    edition = editions.configured()
    assert edition.name == "full"
    assert edition.features == editions.FULL.features | {"skill_kits"}
    assert edition.includes("skill_kits")
    monkeypatch.setenv("DUCKTERM_EDITION", "lite")
    assert editions.configured().features == editions.LITE.features | {"skill_kits"}


def test_an_empty_or_irrelevant_flags_file_keeps_the_preset_itself(home):
    (home / "flags.json").write_text(json.dumps({"enabled": ["shipped_long_ago"]}))
    assert editions.configured() is editions.FULL


@pytest.mark.parametrize("value", ["{", "[]", '{"enabled": "skill_kits"}', '{"enabled": [1]}'])
def test_malformed_flags_fail_startup_like_a_bad_edition(home, value):
    (home / "flags.json").write_text(value)
    with pytest.raises(ValueError, match="feature flags"):
        editions.configured()


def test_kit_routes_belong_to_the_skill_kits_flag():
    for method, url in (
        ("GET", "/harnesses"),
        ("POST", "/harnesses/register"),
        ("GET", "/harnesses/uv-suite/contents"),
        ("DELETE", "/harnesses/uv-suite"),
    ):
        assert editions.request_features(method, url) == ("skill_kits",), url
    assert "skill_kits" not in editions.request_features("GET", "/sessions")


def test_home_layout_belongs_to_the_home_flag():
    assert editions.request_features("GET", "/layouts/home") == ("home",)
    assert editions.request_features("PUT", "/layouts/home") == ("home",)
    assert editions.request_features("PUT", "/layouts/%68ome") == ("home",)
    assert editions.request_features("GET", "/layouts/oracle") == ()


def test_every_flag_has_a_row_in_the_feature_flags_doc():
    text = DOC.read_text()
    for flag in editions.DEVELOPMENT:
        assert f"| `{flag}` |" in text, f"{flag} needs a row in {DOC.name}"


def test_background_rule_capture_requires_explicit_flag(home):
    assert not editions.configured().includes("background_rule_suggestions")
    (home / "flags.json").write_text('{"enabled": ["background_rule_suggestions"]}')
    assert editions.configured().includes("background_rule_suggestions")
    assert editions.request_features("POST", "/agents-md/suggest") == ("rule_suggestions",)
