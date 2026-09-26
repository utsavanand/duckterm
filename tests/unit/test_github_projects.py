import json
import subprocess

import pytest

from duckterm import github_projects as g


@pytest.mark.parametrize(
    "name",
    [
        "../repo",
        "owner/..",
        "https://github.com/o/r",
        "o/r?token=x",
        "o/r/extra",
        "-c x/y",
        "o/r\n",
    ],
)
def test_repository_selection_cannot_change_transport(name):
    with pytest.raises(ValueError):
        g.repository_name(name)


def test_catalog_uses_selected_authorization_and_explicit_pagination(monkeypatch):
    class Response:
        headers = {"Link": '<https://api.github.com/user/repos?page=2>; rel="next"'}

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def read(self, size):
            return json.dumps(
                [
                    {
                        "full_name": "fixture/private",
                        "private": True,
                        "default_branch": "main",
                        "unused": "ignored",
                    }
                ]
            ).encode()

    class Opener:
        def open(self, request, timeout):
            assert request.get_header("Authorization") == "Bearer synthetic-token"
            assert request.full_url.startswith("https://api.github.com/user/repos?")
            assert "page=1" in request.full_url
            return Response()

    monkeypatch.setattr(g.urllib.request, "build_opener", lambda redirect: Opener())
    result = g.repositories("synthetic-token", 1)
    assert result == {
        "repositories": [
            {"full_name": "fixture/private", "private": True, "default_branch": "main"}
        ],
        "next_page": 2,
    }


def test_disabled_connector_does_not_fall_back_to_cli_login(monkeypatch):
    from duckterm import connectors

    monkeypatch.setattr(connectors, "policy", lambda _: {"enabled": False})
    monkeypatch.setattr(
        connectors, "github_token", lambda _: pytest.fail("disabled credential read")
    )
    with pytest.raises(ValueError, match="Enable"):
        g.local_token()


def test_shared_catalog_never_reads_workspace_provider_credentials(monkeypatch):
    from duckterm import connector_client

    monkeypatch.setattr(connector_client, "configured", lambda: True)
    monkeypatch.setattr(g, "local_token", lambda: pytest.fail("workspace token read"))
    monkeypatch.setattr(
        g, "shared_request", lambda operation, params: {"repositories": [], "next_page": None}
    )
    assert g.list_repositories(1)["repositories"] == []


def test_connector_bundle_clones_history_branch_and_clean_origin(tmp_path, monkeypatch):
    from duckterm import connector_client

    origin = tmp_path / "origin"
    origin.mkdir()
    run = subprocess.run

    def git(*args):
        return run(["git", "-C", str(origin), *args], check=True, capture_output=True).stdout

    git("init", "-b", "main")
    git("config", "user.email", "test@example.invalid")
    git("config", "user.name", "Fixture")
    (origin / "README.md").write_text("original\n")
    git("add", "README.md")
    git("commit", "-m", "initial")
    git("checkout", "-b", "feature")
    (origin / "README.md").write_text("feature\n")
    git("commit", "-am", "feature")
    (origin / "README.md").write_text("uncommitted, never cloned\n")

    def fixture_run(args, **kwargs):
        if "--mirror" in args:
            assert "synthetic-token" not in " ".join(args)
            env = kwargs["env"]
            assert env["GIT_CONFIG_KEY_0"] == "http.https://github.com/.extraHeader"
            assert env["GIT_CONFIG_VALUE_1"] == "false"
            assert "GH_TOKEN" not in env
            args = [
                str(origin) if value == "https://github.com/fixture/private.git" else value
                for value in args
            ]
        return run(args, **kwargs)

    monkeypatch.setattr(connector_client, "configured", lambda: False)
    monkeypatch.setattr(
        g, "local_token", lambda: ("synthetic-token", {"enabled": True, "generation": "test"})
    )
    monkeypatch.setattr(g.subprocess, "run", fixture_run)
    destination = tmp_path / "destination"
    g.clone("fixture/private", "feature", destination)
    assert (destination / "README.md").read_text() == "feature\n"
    assert run(
        ["git", "-C", str(destination), "rev-parse", "HEAD"], check=True, capture_output=True
    ).stdout == git("rev-parse", "HEAD")
    config = (destination / ".git/config").read_text()
    assert "https://github.com/fixture/private.git" in config
    assert "synthetic-token" not in config
    assert "extraheader" not in config.lower()
    assert "bundle" not in config


def test_shared_bundle_detects_interrupted_download(tmp_path, monkeypatch):
    import io

    from duckterm import connector_client

    class Stream:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def settimeout(self, timeout):
            pass

        def sendall(self, value):
            assert b"token" not in value

        def makefile(self, mode):
            return io.BytesIO(b'{"bytes":20,"sha256":"unused"}\nshort')

    monkeypatch.setattr(connector_client, "connect", lambda name: (Stream(), b'{"ready":true}\n'))
    with pytest.raises(ValueError, match="interrupted"):
        g.shared_request("bundle", {"repository": "fixture/private"}, tmp_path / "bundle")


def test_selected_repository_cannot_send_connector_auth_to_another_host(tmp_path):
    from duckterm import transfers

    with pytest.raises(ValueError, match="does not match"):
        transfers.clone(
            "44444444444444448444444444444444",
            "https://example.invalid/fixture/private.git",
            "main",
            str(tmp_path / "clone"),
            "fixture/private",
        )
