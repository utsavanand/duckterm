"""Rule review state stays local; repository files never follow symlinks."""

import json
from dataclasses import asdict
from pathlib import Path

import pytest

from duckterm.core.agents_rules import RuleBlock, load_rules, read_catalog, review_path, save_rules


def active() -> RuleBlock:
    return RuleBlock(id="active", text="Reviewed instruction.", status="active")


def test_private_review_never_renders_or_leaks_to_repository(tmp_path: Path) -> None:
    candidate = RuleBlock(id="candidate", text="Unreviewed candidate.")
    proposals = [{"claim": "Unreviewed proposal.", "status": "pending"}]
    save_rules(
        tmp_path, [active(), candidate], proposals=proposals, capture_fingerprint="fingerprint"
    )
    raw = json.loads((tmp_path / ".duckterm-rules.json").read_text())
    assert raw["rules"] == [asdict(active())]
    assert "proposals" not in raw and "capture_fingerprint" not in raw
    assert "Unreviewed" not in (tmp_path / "AGENTS.md").read_text()
    state = read_catalog(tmp_path)
    assert state["proposals"] == proposals and state["capture_fingerprint"] == "fingerprint"
    assert load_rules(tmp_path) == [active(), candidate]
    local = review_path(tmp_path)
    assert not local.is_relative_to(tmp_path)
    assert local.stat().st_mode & 0o777 == 0o600
    assert local.parent.stat().st_mode & 0o777 == 0o700
    assert not (tmp_path / ".duckterm-rules.lock").exists()


def test_background_capture_only_changes_private_state_and_invalidates_stale_save(tmp_path):
    save_rules(tmp_path, [active()])
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    version = read_catalog(tmp_path)["version"]
    save_rules(
        tmp_path,
        [active()],
        proposals=[{"claim": "private"}],
        render=False,
        capture_fingerprint="captured",
        expected_version=version,
    )
    assert {p.name: p.read_bytes() for p in tmp_path.iterdir()} == before
    with pytest.raises(ValueError, match="changed"):
        save_rules(tmp_path, [active()], expected_version=version)


def test_legacy_review_state_migrates_on_owner_save(tmp_path):
    candidate = RuleBlock(id="legacy", text="Legacy unreviewed candidate.")
    (tmp_path / ".duckterm-rules.json").write_text(
        json.dumps(
            {
                "rules": [asdict(active()), asdict(candidate)],
                "proposals": [{"claim": "private"}],
                "capture_fingerprint": "legacy-fingerprint",
            }
        )
    )
    state = read_catalog(tmp_path)
    save_rules(tmp_path, load_rules(tmp_path), expected_version=state["version"])
    after = read_catalog(tmp_path)
    assert after["proposals"] == state["proposals"]
    assert after["capture_fingerprint"] == "legacy-fingerprint"
    raw = json.loads((tmp_path / ".duckterm-rules.json").read_text())
    assert raw["rules"] == [asdict(active())]
    assert "proposals" not in raw and "capture_fingerprint" not in raw


def test_review_state_is_separate_for_each_canonical_folder(tmp_path):
    one, two = tmp_path / "one", tmp_path / "two"
    one.mkdir()
    two.mkdir()
    save_rules(one, [], proposals=[{"claim": "one"}])
    save_rules(two, [], proposals=[{"claim": "two"}])
    alias = tmp_path / "alias"
    alias.symlink_to(one, target_is_directory=True)
    assert review_path(alias) == review_path(one)
    assert read_catalog(alias)["proposals"] == [{"claim": "one"}]
    assert read_catalog(two)["proposals"] == [{"claim": "two"}]


def test_unmanaged_file_requires_explicit_replacement_and_keeps_backup(tmp_path):
    path = tmp_path / "AGENTS.md"
    original = "Handwritten instructions that must survive.\n"
    path.write_text(original)
    before = read_catalog(tmp_path)
    assert before["drift"] and not before["managed"]
    with pytest.raises(ValueError, match="differs"):
        save_rules(tmp_path, [active()])
    assert path.read_text() == original
    assert not (tmp_path / ".duckterm-rules.json").exists()
    save_rules(tmp_path, [active()], expected_version=before["version"], reconcile_drift=True)
    assert next(tmp_path.glob("AGENTS.md.before-*")).read_text() == original
    assert read_catalog(tmp_path)["managed"]


@pytest.mark.parametrize("name", ["AGENTS.md", ".duckterm-rules.json"])
@pytest.mark.parametrize("dangling", [False, True])
def test_symlinked_sources_are_never_read_or_backed_up(tmp_path, name, dangling):
    repo = tmp_path / "repo"
    repo.mkdir()
    target = tmp_path / "private"
    if not dangling:
        target.write_text("SECRET")
    (repo / name).symlink_to(target)
    with pytest.raises(ValueError, match="symlink"):
        read_catalog(repo)
    with pytest.raises(ValueError, match="symlink"):
        save_rules(repo, [active()], reconcile_drift=True)
    assert not list(repo.glob("AGENTS.md.before-*"))
    assert target.exists() is (not dangling)
    if not dangling:
        assert target.read_text() == "SECRET"


def test_repository_lock_symlink_is_ignored(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    target = tmp_path / "must-not-be-created"
    (repo / ".duckterm-rules.lock").symlink_to(target)
    save_rules(repo, [active()])
    assert not target.exists()


def test_private_lock_symlink_is_refused(tmp_path):
    local = review_path(tmp_path)
    local.parent.mkdir(parents=True, exist_ok=True)
    target = tmp_path / "must-not-be-created"
    local.with_suffix(".lock").symlink_to(target)
    with pytest.raises(OSError):
        save_rules(tmp_path, [active()])
    assert not target.exists()
