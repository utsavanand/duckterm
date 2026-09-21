"""The request gate's building blocks: origin/token checks and the input
validators that guard values flowing into shells, paths, and AppleScript.
Before this, every test sent the VALID token — nothing asserted a REJECTION, so
weakening the gate (loosened regex, inverted check) left the suite green."""

import stat

import pytest

from duckterm.helpers import security


class TestOriginAllowed:
    def test_absent_origin_and_referer_passes(self) -> None:
        # curl / same-origin fetches send neither header.
        assert security.origin_allowed({}) is True

    @pytest.mark.parametrize(
        "origin",
        [
            "http://127.0.0.1:4300",
            "http://localhost:4300",
            "http://[::1]:4300",
            "http://127.0.0.1",
            "http://localhost:9999",  # custom port with matching Host
        ],
    )
    def test_localhost_origins_pass(self, origin: str) -> None:
        assert security.origin_allowed({"origin": origin, "host": origin[7:]}) is True

    @pytest.mark.parametrize(
        "origin",
        [
            "http://evil.test",
            "https://duckterm.evil.com",
            "http://127.0.0.1.evil.com",  # suffix trick
            "http://notlocalhost",
            "null",  # sandboxed iframe sends Origin: null
            "http://localhost.evil.com",
        ],
    )
    def test_foreign_origins_are_rejected(self, origin: str) -> None:
        assert security.origin_allowed({"origin": origin}) is False

    def test_referer_prefix_is_checked_when_no_origin(self) -> None:
        assert (
            security.origin_allowed(
                {"referer": "http://localhost:4300/dashboard", "host": "localhost:4300"}
            )
            is True
        )
        assert security.origin_allowed({"referer": "http://evil.test/x"}) is False

    def test_origin_wins_over_referer(self) -> None:
        # A present Origin is authoritative; a good referer can't rescue a bad origin.
        headers = {"origin": "http://evil.test", "referer": "http://localhost:4300/"}
        assert security.origin_allowed(headers) is False


class TestTokenValid:
    def test_correct_token_passes(self) -> None:
        assert security.token_valid({security.TOKEN_HEADER: "sekret"}, "sekret") is True

    def test_wrong_token_rejected(self) -> None:
        assert security.token_valid({security.TOKEN_HEADER: "nope"}, "sekret") is False

    def test_missing_token_header_rejected(self) -> None:
        assert security.token_valid({}, "sekret") is False

    def test_empty_token_rejected(self) -> None:
        assert security.token_valid({security.TOKEN_HEADER: ""}, "sekret") is False


class TestValidators:
    @pytest.mark.parametrize("key", ["abc", "new-1a2b", "A.b_c-9", "x" * 128])
    def test_valid_session_keys(self, key: str) -> None:
        assert security.valid_session_key(key) is True

    @pytest.mark.parametrize(
        "key",
        ["", None, ".", "..", "../etc", "a b", "a;rm -rf", "a/b", "x" * 129, "a$b", "a`b`"],
    )
    def test_invalid_session_keys_rejected(self, key: str | None) -> None:
        assert security.valid_session_key(key) is False

    def test_valid_tty(self) -> None:
        assert security.valid_tty("/dev/ttys042") is True

    @pytest.mark.parametrize(
        "tty",
        ["", None, "/dev/tty; rm -rf /", "/dev/pts/0", "ttys0", "/dev/ttyABC!"],
    )
    def test_invalid_tty_rejected(self, tty: str | None) -> None:
        assert security.valid_tty(tty) is False

    def test_valid_snapshot_id(self) -> None:
        assert security.valid_snapshot_id("snap-1785000000") is True

    @pytest.mark.parametrize(
        "sid", ["", None, "snap-1x", "snap-", "../snap-1", "snapshot-1", "snap-1;rm"]
    )
    def test_invalid_snapshot_id_rejected(self, sid: str | None) -> None:
        assert security.valid_snapshot_id(sid) is False


class TestToken:
    def test_token_is_created_0600_and_reused(self, tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
        monkeypatch.setenv("DUCKTERM_HOME", str(tmp_path))
        first = security.load_or_create_token()
        assert first  # non-empty
        mode = stat.S_IMODE((tmp_path / "token").stat().st_mode)
        assert mode == 0o600  # not world/group readable
        assert security.load_or_create_token() == first  # stable across calls

    def test_new_session_key_is_unguessable_and_prefixed(self) -> None:
        a = security.new_session_key("new")
        b = security.new_session_key("new")
        assert a.startswith("new-") and b.startswith("new-")
        assert a != b  # 64 bits of entropy — collisions don't happen
        assert security.valid_session_key(a)  # its own output passes the validator

    def test_secret_write_replaces_symlink_without_overwriting_target(self, tmp_path) -> None:
        target = tmp_path / "original"
        target.write_text("untouched")
        secret = tmp_path / "secret"
        secret.symlink_to(target)
        security.write_private_text(secret, "new credential")
        assert target.read_text() == "untouched"
        assert not secret.is_symlink()
        assert secret.read_text() == "new credential"
        assert stat.S_IMODE(secret.stat().st_mode) == 0o600


@pytest.mark.parametrize(
    "host",
    [
        "",
        "attacker.test",
        "localhost.attacker.test",
        "localhost:99999",
        "localhost:0",
        "localhost\n",
    ],
)
def test_invalid_hosts_are_rejected(host: str) -> None:
    assert security.host_allowed(host) is False


@pytest.mark.parametrize(
    "origin", ["http://localhost:8000", "https://localhost:4300", "http://127.0.0.1:4300", "null"]
)
def test_other_origins_are_rejected_even_when_local(origin: str) -> None:
    assert security.origin_allowed({"host": "localhost:4300", "origin": origin}) is False
