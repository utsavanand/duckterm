"""Inbox reads must surface all open work and report this read honestly (QA
audit relayed by main-dev, 2026-10-04)."""

import time

from tests.runtime.test_session_api import call, enroll, store  # noqa: F401


def owner_question(store, key, n):  # noqa: F811
    return store.session_api.owner_message(
        key, f"Owner question {n}", request_key=f"q{n}", question=True
    )


def test_open_work_older_than_the_first_page_still_shows(store) -> None:  # noqa: F811
    creds = enroll(store, "b")
    old = owner_question(store, "b", 0)
    for n in range(1, 61):  # 60 newer messages, all answered
        call(
            store,
            creds,
            "POST",
            f"/questions/{owner_question(store, 'b', n)}/answer",
            {"text": "done"},
        )
    page = call(store, creds, "GET", "/inbox")[1]
    pending = [m for m in page["messages"] if m["status"] == "queued"]
    assert [(m["id"], m.get("older_pending")) for m in pending] == [(old, True)]
    assert page["next_cursor"] is not None  # history paging is unchanged


def test_the_response_reports_this_read_not_the_previous_one(store) -> None:  # noqa: F811
    creds = enroll(store, "b")
    owner_question(store, "b", 0)
    before = int(time.time() * 1000)
    (message,) = call(store, creds, "GET", "/inbox")[1]["messages"]
    assert message["delivery"]["last_read_at"] >= before


def test_a_read_but_unanswered_owner_question_is_not_called_unread(store) -> None:  # noqa: F811
    creds = enroll(store, "b")
    owner_question(store, "b", 0)
    owner_question(store, "b", 1)
    store.session_api.owner_message("b", "Unread note")
    call(store, creds, "GET", "/inbox")  # reads both questions and the note
    owner_question(store, "b", 2)  # arrives after the read
    notice = store.session_api.turn_end_notice("b")
    assert notice.startswith(
        "You have 1 unread owner message(s), 2 owner message(s) you have read but not answered."
    )
