"""Approved continuity flows use the same real handlers in every edition."""

from pathlib import Path

import pytest
from tests.runtime import test_checkpoint_plan as checkpoint_cases
from tests.runtime import test_handoff_review as handoff_cases
from tests.runtime import test_memory as memory_cases
from tests.runtime.test_memory import memory_rig
from tests.runtime.test_saved_checkpoint import rig

from duckterm.editions import CODE, FULL, LITE

__all__ = ["memory_rig", "rig"]


@pytest.fixture(params=[LITE, CODE, FULL], ids=lambda edition: edition.name, autouse=True)
def edition(request, monkeypatch, tmp_path):
    # The shared functional fixtures construct both persistence and Server after
    # this selection; no Full-initialized object is relabelled to simulate Lite.
    monkeypatch.setenv("DUCKTERM_EDITION", request.param.name)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    return request.param


def assert_edition(server, edition):
    assert server.edition is edition
    assert server.history.session_api.edition is edition


def test_checkpoint_plan_and_selected_summary_preserve_http_auth(memory_rig, monkeypatch, edition):
    assert_edition(memory_rig[0], edition)
    checkpoint_cases.test_http_plan_is_read_only_and_submit_keeps_write_auth(
        memory_rig, monkeypatch
    )


def test_memory_retains_originals_across_harnesses_and_cleanup(memory_rig, edition):
    assert_edition(memory_rig[0], edition)
    memory_cases.test_three_generations_and_original_text_survive_source_cleanup_and_reopen(
        memory_rig
    )


def test_context_is_still_session_scoped(memory_rig, edition):
    assert_edition(memory_rig[0], edition)
    memory_cases.test_session_credential_cannot_read_peers_even_in_same_folder(memory_rig)


def test_handoff_review_requires_owner_and_explicit_approval(rig, edition):
    assert_edition(rig[0], edition)
    handoff_cases.test_review_routes_require_owner_and_explicit_approval(rig)


def test_approved_handoff_is_durable_and_idempotent(rig, tmp_path, edition):
    assert_edition(rig[0], edition)
    handoff_cases.test_owner_http_approval_is_idempotent_and_persists_checkpoint(rig, tmp_path)


def test_approved_brief_prepares_continuity_without_an_available_model(rig, monkeypatch, edition):
    assert_edition(rig[0], edition)
    handoff_cases.test_long_reviewed_brief_uses_one_revision_without_provider_and_can_prepare(
        rig, monkeypatch
    )
