"""One DUCKTERM_INSTANCE isolates a whole DuckTerm instance: home, tmux
socket, port, and callback URL all derive from it, distinctly per id, while an
explicit per-facet env var still wins (tests/e2e depend on that)."""

import http.server
import threading
import urllib.error
import urllib.request
from collections.abc import Iterator

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
    monkeypatch.setenv("DUCKTERM_URL", "http://localhost:1234")
    assert str(paths.home()) == "/tmp/custom-home"
    assert tmux.socket_name() == "custom-sock"
    assert instance.port() == 9999
    assert instance.server_url() == "http://localhost:1234"


@pytest.mark.parametrize(
    "url",
    [
        "https://attacker.example",
        "http://attacker.example:4300",
        "http://127.0.0.1:4300@attacker.example",
        "http://127.0.0.1.attacker.example:4300",
        "http://127.0.0.1:4300/../x",
    ],
)
def test_an_off_machine_url_override_is_ignored(monkeypatch: pytest.MonkeyPatch, url: str) -> None:
    # The CLI sends the owner token to server_url(); a repository's project
    # settings can set DUCKTERM_URL for every agent it runs.
    monkeypatch.setenv("DUCKTERM_PORT", "4555")
    monkeypatch.setenv("DUCKTERM_URL", url)
    assert instance.server_url() == "http://127.0.0.1:4555"


def test_a_rejected_override_warns_without_echoing_it(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("DUCKTERM_PORT", "4555")
    monkeypatch.setenv("DUCKTERM_URL", "http://user:secret@evil.example\x1b]0;x")
    assert instance.server_url() == "http://127.0.0.1:4555"
    err = capsys.readouterr().err
    assert "ignoring DUCKTERM_URL" in err
    assert "secret" not in err and "evil" not in err and "\x1b" not in err


@pytest.fixture
def listeners() -> Iterator[tuple[dict[str, list[str]], int, int]]:
    """A loopback server that redirects every request to a would-be proxy."""
    seen: dict[str, list[str]] = {"server": [], "proxy": []}
    ports: list[int] = []
    servers = []
    for name in seen:

        class Handler(http.server.BaseHTTPRequestHandler):
            role = name

            def do_GET(self) -> None:
                seen[self.role].append(self.headers.get("X-Duckterm-Token", ""))
                if self.role == "server" and self.path == "/redirect":
                    self.send_response(302)
                    self.send_header("Location", f"http://127.0.0.1:{ports[1]}/")
                else:
                    self.send_response(200)
                self.end_headers()

            def log_message(self, *args: object) -> None:
                pass

        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        servers.append(server)
        ports.append(server.server_address[1])
    yield seen, ports[0], ports[1]
    for server in servers:
        server.shutdown()
        server.server_close()


def test_local_requests_ignore_proxies_and_redirects(
    monkeypatch: pytest.MonkeyPatch, listeners: tuple[dict[str, list[str]], int, int]
) -> None:
    # A repository's .claude/settings.json "env" can set proxy variables for
    # every command an agent runs, including `duckterm session ...`.
    seen, server_port, proxy_port = listeners
    for name in ("http_proxy", "HTTP_PROXY", "all_proxy", "ALL_PROXY"):
        monkeypatch.setenv(name, f"http://127.0.0.1:{proxy_port}")
    monkeypatch.setenv("no_proxy", "")
    headers = {"X-Duckterm-Token": "fixture-token"}
    base = f"http://127.0.0.1:{server_port}"
    instance.open_local(urllib.request.Request(base + "/", headers=headers), timeout=5).read()
    with pytest.raises(urllib.error.HTTPError) as redirect:
        instance.open_local(urllib.request.Request(base + "/redirect", headers=headers), timeout=5)
    assert redirect.value.code == 302
    assert seen == {"server": ["fixture-token", "fixture-token"], "proxy": []}


def test_invalid_instance_id_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    # A path-traversal id must not escape into home()/the socket name.
    monkeypatch.setenv("DUCKTERM_INSTANCE", "../evil")
    with pytest.raises(ValueError, match="invalid DUCKTERM_INSTANCE"):
        instance.home()
