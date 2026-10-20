"""Name lookup never guesses between visible peers or incomplete directories."""

import io
import json

import pytest

from duckterm.session_client import _resolve_target


def directory(monkeypatch, pages):
    requests = []
    iterator = iter(pages)

    def open_page(request, timeout):
        requests.append(request)
        return io.BytesIO(json.dumps(next(iterator)).encode())

    monkeypatch.setattr("duckterm.session_client.urllib.request.urlopen", open_page)
    return requests


def test_name_resolution_reads_all_pages_and_returns_remote_reference(monkeypatch):
    reference = "peer:computer:remote"
    requests = directory(
        monkeypatch,
        [
            {"sessions": [{"session_id": "local", "name": "qa"}], "next_cursor": "page 2"},
            {"sessions": [{"session_id": "remote", "session_ref": reference, "name": "product"}]},
        ],
    )
    assert _resolve_target("http://127.0.0.1:4300", {}, "product") == reference
    assert "scope=shared_root" in requests[0].full_url
    assert "cursor=page+2" in requests[1].full_url


def test_same_name_on_another_page_is_ambiguous(monkeypatch):
    directory(
        monkeypatch,
        [
            {"sessions": [{"session_id": "one", "name": "product"}], "next_cursor": "next"},
            {"sessions": [{"session_id": "two", "name": "product"}]},
        ],
    )
    with pytest.raises(ValueError, match="ambiguous"):
        _resolve_target("http://127.0.0.1:4300", {}, "product")


def test_local_unique_name_and_id_precedence(monkeypatch):
    cards = [{"session_id": "product", "name": "renamed"}, {"session_id": "two", "name": "product"}]
    directory(monkeypatch, [{"sessions": cards}])
    assert _resolve_target("http://127.0.0.1:4300", {}, "product") == "product"
    directory(monkeypatch, [{"sessions": [{"session_id": "two", "name": "product"}]}])
    assert _resolve_target("http://127.0.0.1:4300", {}, "product") == "two"


@pytest.mark.parametrize("target", ["a" * 32, "peer:computer:session"])
def test_explicit_ids_do_not_require_directory_availability(monkeypatch, target):
    requests = directory(monkeypatch, [])
    assert _resolve_target("http://127.0.0.1:4300", {}, target) == target
    assert not requests


def test_incomplete_remote_directory_never_resolves_local_name(monkeypatch):
    directory(
        monkeypatch,
        [
            {
                "cross_computer_status": "unavailable",
                "sessions": [{"session_id": "one", "name": "product"}],
            }
        ],
    )
    with pytest.raises(ValueError, match="remote discovery is unavailable"):
        _resolve_target("http://127.0.0.1:4300", {}, "product")


def test_repeated_cursor_fails_closed(monkeypatch):
    directory(monkeypatch, [{"sessions": [], "next_cursor": "same"}] * 2)
    with pytest.raises(ValueError, match="repeated a cursor"):
        _resolve_target("http://127.0.0.1:4300", {}, "product")


def test_archived_duplicates_do_not_hide_the_live_name(monkeypatch):
    directory(
        monkeypatch,
        [
            {
                "sessions": [
                    {"session_id": "old1", "name": "kaho-remote", "state": "archived"},
                    {"session_id": "old2", "name": "kaho-remote", "state": "archived"},
                    {
                        "session_id": "live",
                        "session_ref": "peer:remote:live",
                        "name": "kaho-remote",
                        "state": "idle",
                    },
                ]
            }
        ],
    )
    assert _resolve_target("http://127.0.0.1:4300", {}, "kaho-remote") == "peer:remote:live"
