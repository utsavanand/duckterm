"""Independent pagination and malformed-cursor checks."""

import base64

import pytest
from tests.runtime.test_timeline import read, stores  # noqa: F401


def test_reused_rowid_cannot_enter_an_existing_pagination(stores):  # noqa: F811
    history, _ = stores
    for i in range(3):
        history.record(
            {
                "_id": str(i),
                "_ts": 10,
                "event_type": "UserPromptSubmit",
                "session_key": "a",
                "prompt": "Original",
                "test": True,
            }
        )
    first = read(stores, limit=1, kinds="prompt")
    history._conn.execute("DELETE FROM events WHERE id='2'")
    history._conn.commit()
    history.record(
        {
            "_id": "new",
            "_ts": 5,
            "event_type": "UserPromptSubmit",
            "session_key": "a",
            "prompt": "Inserted after first page",
            "test": True,
        }
    )
    # Without a retained snapshot, rejecting a retired high-water anchor is
    # safer than admitting a later insert into this pagination sequence.
    with pytest.raises(ValueError, match="cursor"):
        read(stores, limit=10, kinds="prompt", before=first["next_cursor"])


def test_deeply_nested_cursor_is_rejected_as_invalid(stores):  # noqa: F811
    value = base64.urlsafe_b64encode(("[" * 1500 + "0" + "]" * 1500).encode()).decode()
    with pytest.raises(ValueError, match="cursor"):
        read(stores, before=value)


def test_cursor_and_source_rows_stay_bound_to_session(stores):  # noqa: F811
    from duckterm.persistence.timeline import page

    history, digest = stores
    for key in ("a", "b"):
        for i in range(2):
            history.record(
                {
                    "_id": f"{key}-{i}",
                    "_ts": 10,
                    "event_type": "UserPromptSubmit",
                    "session_key": key,
                    "prompt": f"{key} private text",
                    "test": True,
                }
            )
    first = read(stores, limit=1, kinds="prompt")
    assert first["summary"]["total"] == 2
    assert first["entries"][0]["detail"]["text"] == "a private text"
    with pytest.raises(ValueError, match="cursor"):
        page(
            history._conn,
            digest._conn,
            history.session("b"),
            [],
            now=1000,
            kinds="prompt",
            before=first["next_cursor"],
        )
