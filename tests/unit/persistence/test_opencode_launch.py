import json
import sqlite3
import time
from dataclasses import replace

import pytest

from duckterm.persistence import opencode_launch as journal
from duckterm.runtimes.opencode_identity import Selection
from duckterm.runtimes.opencode_protocol import OpenCodeError


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "fixture.sqlite"
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE sessions (session_key TEXT PRIMARY KEY, runtime TEXT, restart_json TEXT)"
    )
    conn.execute(
        "INSERT INTO sessions VALUES ('fixture', 'opencode', ?)",
        (json.dumps({"unrelated": {"keep": True}}),),
    )
    conn.commit()
    yield conn, path
    conn.close()


def start(db, tmp_path, prompt="initial task"):
    conn, _ = db
    state = journal.Launch(
        "generation",
        "local",
        "/native/store",
        str(tmp_path.resolve()),
        12,
        prompt=prompt,
        model="fixture/custom/model",
    )
    return journal.begin(conn, "fixture", state)


def bind(conn, state):
    journal.claim_create(conn, "fixture", state.generation)
    return journal.bind_created(
        conn, "fixture", state.generation, {"id": "ses_owned", "directory": state.directory}
    )


def selected(state):
    return Selection(
        state.generation,
        state.directory,
        state.pid,
        state.session_id,
        sequence=1,
        selected=True,
        observed_at=time.monotonic(),
    )


def receipt(state, **changes):
    provider, model = state.model.split("/", 1)
    return {
        "info": {
            "id": state.message_id,
            "sessionID": state.session_id,
            "role": "user",
            "model": {"providerID": provider, "modelID": model},
            **changes,
        },
        "parts": [
            {
                "id": "prt_fixture",
                "sessionID": state.session_id,
                "messageID": state.message_id,
                "type": "text",
                "text": state.prompt,
            }
        ],
    }


@pytest.mark.parametrize(
    "crash_phase", ["prepared", "creating", "bound", "selecting", "submitting", "ready"]
)
def test_restart_preserves_each_boundary_and_never_reclaims_writes(db, tmp_path, crash_phase):
    conn, path = db
    state = start(db, tmp_path)
    if crash_phase != "prepared":
        state = (
            bind(conn, state)
            if crash_phase != "creating"
            else journal.claim_create(conn, "fixture", state.generation)
        )
    if crash_phase in {"selecting", "submitting", "ready"}:
        state = journal.claim_select(conn, "fixture", state.generation)
    if crash_phase in {"submitting", "ready"}:
        state = journal.claim_prompt(conn, "fixture", state.generation, selected(state))
    if crash_phase == "ready":
        state = journal.reconcile_prompt(conn, "fixture", state.generation, receipt(state))

    recovered = sqlite3.connect(path)
    try:
        assert journal.read(recovered, "fixture", state.generation) == state
        assert state.phase == crash_phase
        metadata = json.loads(recovered.execute("SELECT restart_json FROM sessions").fetchone()[0])
        assert metadata["unrelated"] == {"keep": True}
        with pytest.raises(OpenCodeError, match="already has"):
            journal.begin(
                recovered,
                "fixture",
                replace(state, phase="prepared", session_id=None, message_id=None),
            )
        if crash_phase != "prepared":
            with pytest.raises(OpenCodeError, match="already claimed"):
                journal.claim_create(recovered, "fixture", state.generation)
        if crash_phase in {"submitting", "ready"}:
            with pytest.raises(OpenCodeError, match="already claimed"):
                journal.claim_prompt(recovered, "fixture", state.generation, selected(state))
    finally:
        recovered.close()


def test_only_one_process_can_claim_submission(db, tmp_path):
    conn, path = db
    state = bind(conn, start(db, tmp_path))
    journal.claim_select(conn, "fixture", state.generation)
    competing = sqlite3.connect(path)
    try:
        claimed = journal.claim_prompt(competing, "fixture", state.generation, selected(state))
        assert claimed.message_id == state.message_id
        with pytest.raises(OpenCodeError, match="already claimed"):
            journal.claim_prompt(conn, "fixture", state.generation, selected(state))
    finally:
        competing.close()


@pytest.mark.parametrize(
    "invalid",
    [
        None,
        {},
        {"id": "foreign"},
        {"role": "assistant"},
        {"sessionID": "ses_other"},
        {"model": {"providerID": "other", "modelID": "model"}},
    ],
)
def test_lost_or_wrong_receipt_leaves_submission_uncertain(db, tmp_path, invalid):
    conn, _ = db
    state = bind(conn, start(db, tmp_path))
    journal.claim_select(conn, "fixture", state.generation)
    state = journal.claim_prompt(conn, "fixture", state.generation, selected(state))
    evidence = receipt(state, **invalid) if invalid else invalid
    with pytest.raises(OpenCodeError):
        journal.reconcile_prompt(conn, "fixture", state.generation, evidence)
    assert journal.read(conn, "fixture", state.generation).phase == "submitting"
    recovered = journal.reconcile_prompt(conn, "fixture", state.generation, receipt(state))
    assert recovered.phase == "ready"


def test_fresh_native_selection_required_even_after_selection_http_receipt(db, tmp_path):
    conn, _ = db
    state = bind(conn, start(db, tmp_path))
    journal.claim_select(conn, "fixture", state.generation)
    proof = selected(state)
    for wrong in [
        replace(proof, selected=False),
        replace(proof, contested=True),
        replace(proof, observed_at=time.monotonic() - 6),
        replace(proof, generation="old"),
        replace(proof, pid=13),
        replace(proof, session_id="ses_other"),
    ]:
        with pytest.raises(OpenCodeError):
            journal.claim_prompt(conn, "fixture", state.generation, wrong)
    assert journal.read(conn, "fixture", state.generation).phase == "selecting"


def test_no_prompt_needs_selection_but_creates_no_message(db, tmp_path):
    conn, _ = db
    state = bind(conn, start(db, tmp_path, prompt=""))
    journal.claim_select(conn, "fixture", state.generation)
    state = journal.claim_prompt(conn, "fixture", state.generation, selected(state))
    assert state.phase == "ready" and state.message_id is None
    with pytest.raises(OpenCodeError):
        state.prompt_body()


def test_foreign_creation_and_old_generation_cannot_bind_card(db, tmp_path):
    conn, _ = db
    state = start(db, tmp_path)
    journal.claim_create(conn, "fixture", state.generation)
    for data in [
        {"id": "ses_new", "directory": "/wrong"},
        {"id": "ses_new", "directory": state.directory, "parentID": "ses_parent"},
    ]:
        with pytest.raises(OpenCodeError):
            journal.bind_created(conn, "fixture", state.generation, data)
    with pytest.raises(OpenCodeError, match="older launch"):
        journal.bind_created(
            conn, "fixture", "old", {"id": "ses_new", "directory": state.directory}
        )
    assert journal.read(conn, "fixture", state.generation).phase == "creating"


def test_duplicate_binding_is_scoped_to_destination_and_store(db, tmp_path):
    conn, _ = db
    state = start(db, tmp_path)
    journal.claim_create(conn, "fixture", state.generation)
    binding = {"native_id": "ses_owned", "destination": state.destination, "store": state.store}
    conn.execute(
        "INSERT INTO sessions VALUES ('other', 'opencode', ?)",
        (json.dumps({"native_binding": binding}),),
    )
    conn.commit()
    result = {"id": "ses_owned", "directory": state.directory}
    with pytest.raises(OpenCodeError, match="another card"):
        journal.bind_created(conn, "fixture", state.generation, result)
    binding["store"] = "/different/store"
    conn.execute(
        "UPDATE sessions SET restart_json=? WHERE session_key='other'",
        (json.dumps({"native_binding": binding}),),
    )
    conn.commit()
    assert journal.bind_created(conn, "fixture", state.generation, result).phase == "bound"


def test_failed_commit_makes_no_write_claim_and_preserves_metadata(db, tmp_path):
    conn, _ = db
    state = start(db, tmp_path)
    conn.execute(
        "CREATE TRIGGER reject_update BEFORE UPDATE ON sessions "
        "BEGIN SELECT RAISE(ABORT, 'disk failure'); END"
    )
    conn.commit()
    with pytest.raises(sqlite3.DatabaseError):
        journal.claim_create(conn, "fixture", state.generation)
    assert journal.read(conn, "fixture", state.generation).phase == "prepared"


def test_changed_binding_or_harness_rejects_delayed_result(db, tmp_path):
    conn, _ = db
    state = bind(conn, start(db, tmp_path))
    control = json.loads(conn.execute("SELECT restart_json FROM sessions").fetchone()[0])
    control["native_binding"]["generation"] = "replacement"
    conn.execute("UPDATE sessions SET restart_json=?", (json.dumps(control),))
    conn.commit()
    with pytest.raises(OpenCodeError, match="binding has changed"):
        journal.claim_select(conn, "fixture", state.generation)
    conn.execute("UPDATE sessions SET runtime='codex'")
    conn.commit()
    with pytest.raises(OpenCodeError, match="no longer owns"):
        journal.claim_select(conn, "fixture", state.generation)
