"""Checkpoint settings validate limits, persist per instance, and require owner auth."""

import json
from concurrent.futures import ThreadPoolExecutor

import pytest
from test_memory import memory_rig
from test_session_api import dispatch

from duckterm import checkpoint_settings
from duckterm.core.session_api import APIError

__all__ = ["memory_rig"]


def test_defaults_and_partial_concurrent_updates_survive_reload(memory_rig):
    assert checkpoint_settings.load() == checkpoint_settings.DEFAULTS
    assert checkpoint_settings.DEFAULTS["lookback_records"] == "all"
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(checkpoint_settings.save, value)
            for value in ({"lookback_records": 60}, {"writer_model": "sonnet"})
        ]
        for future in futures:
            future.result()
    result = checkpoint_settings.load()
    assert result["lookback_records"] == 60
    assert result["writer_model"] == "sonnet"
    assert "batch_records" not in result
    assert checkpoint_settings.save({"lookback_records": "all"})["lookback_records"] == "all"


def test_settings_file_with_retired_keys_still_loads(memory_rig):
    from duckterm.helpers import paths

    (paths.home() / "checkpoint-settings.json").write_text(
        json.dumps(
            {
                "lookback_records": 60,
                "batch_records": 20,
                "reviewer_model": "opus",
                "writer_model": "sonnet",
            }
        )
    )
    loaded = checkpoint_settings.load()
    assert loaded == {
        **checkpoint_settings.DEFAULTS,
        "lookback_records": 60,
        "writer_model": "sonnet",
    }
    assert checkpoint_settings.save({"writer_model": "opus"})["lookback_records"] == 60
    saved = json.loads((paths.home() / "checkpoint-settings.json").read_text())
    assert "batch_records" not in saved and "reviewer_model" not in saved


@pytest.mark.parametrize(
    "value",
    [
        [],
        None,
        {"lookback_records": True},
        {"lookback_records": 0},
        {"lookback_records": 1001},
        {"lookback_records": "20"},
        {"lookback_records": "ALL"},
        {"batch_records": 20},
        {"writer_model": []},
        {"reviewer_model": "invalid"},
        {"unexpected": 1},
    ],
)
def test_invalid_values_do_not_change_settings(memory_rig, value):
    before = checkpoint_settings.load()
    with pytest.raises(APIError):
        checkpoint_settings.save(value)
    assert checkpoint_settings.load() == before


def test_settings_routes_require_owner_and_validate_payload(memory_rig):
    server, agent, _, _ = memory_rig
    for method in ("GET", "POST"):
        assert dispatch(server, method, "/checkpoint-settings", {})[0] == 401
        assert dispatch(server, method, "/checkpoint-settings", agent)[0] == 403
    owner = {"x-duckterm-token": server.token}
    assert dispatch(server, "GET", "/checkpoint-settings", owner) == (
        200,
        checkpoint_settings.DEFAULTS,
    )
    status, result = dispatch(
        server, "POST", "/checkpoint-settings", owner, json.dumps({"lookback_records": 60}).encode()
    )
    assert status == 200 and result["lookback_records"] == 60
    assert dispatch(server, "POST", "/checkpoint-settings", owner, b"{bad")[0] == 400
