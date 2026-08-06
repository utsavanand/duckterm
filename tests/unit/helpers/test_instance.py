"""One DUCKTERM_INSTANCE isolates a whole RubberTerm instance: home, tmux
socket, port, and callback URL all derive from it, distinctly per id, while an
explicit per-facet env var still wins (tests/e2e depend on that)."""

import pytest

from duckterm.agents import tmux
from duckterm.helpers import instance, paths


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for k in (
        "DUCKTERM_INSTANCE",
        "DUCKTERM_HOME",
        "DUCKTERM_TMUX_SOCKET",
        "DUCKTERM_PORT",
        "DUCKTERM_URL",
    ):
        monkeypatch.delenv(k, raising=False)


def test_legacy_no_instance_is_unchanged() -> None:
    assert paths.home().name == ".duckterm"
    assert tmux.socket_name() == "duckterm"
    assert instance.port() == 4300
    assert instance.server_url() == "http://127.0.0.1:4300"


def test_one_instance_id_isolates_all_four_facets(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DUCKTERM_INSTANCE", "dev")
    assert paths.home().name == ".duckterm-dev"
    assert tmux.socket_name() == "duckterm-dev"
    assert instance.port() != 4300
    assert instance.server_url() == f"http://127.0.0.1:{instance.port()}"


def test_distinct_instances_never_collide(monkeypatch: pytest.MonkeyPatch) -> None:
    facets = {}
    for iid in ("prod", "dev", "beta"):
        monkeypatch.setenv("DUCKTERM_INSTANCE", iid)
        facets[iid] = (str(paths.home()), tmux.socket_name(), instance.port())
    homes = {f[0] for f in facets.values()}
    socks = {f[1] for f in facets.values()}
    ports = {f[2] for f in facets.values()}
    assert len(homes) == 3
    assert len(socks) == 3
    assert len(ports) == 3  # the collision that would let one instance kill another


def test_port_is_stable_for_a_given_id(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DUCKTERM_INSTANCE", "beta")
    assert instance.port() == instance.port()  # deterministic, not random


def test_explicit_overrides_still_win(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DUCKTERM_INSTANCE", "dev")
    monkeypatch.setenv("DUCKTERM_HOME", "/tmp/custom-home")
    monkeypatch.setenv("DUCKTERM_TMUX_SOCKET", "custom-sock")
    monkeypatch.setenv("DUCKTERM_PORT", "9999")
    monkeypatch.setenv("DUCKTERM_URL", "http://example.test:1234")
    assert str(paths.home()) == "/tmp/custom-home"
    assert tmux.socket_name() == "custom-sock"
    assert instance.port() == 9999
    assert instance.server_url() == "http://example.test:1234"


def test_invalid_instance_id_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    # A path-traversal id must not escape into home()/the socket name.
    monkeypatch.setenv("DUCKTERM_INSTANCE", "../evil")
    with pytest.raises(ValueError, match="invalid DUCKTERM_INSTANCE"):
        instance.home()
