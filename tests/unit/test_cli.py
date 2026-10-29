import pytest

from duckterm import __version__
from duckterm.cli import main


def test_version_flag_prints_version_and_exits_zero(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    assert __version__ in capsys.readouterr().out


def test_no_command_prints_help_and_succeeds(capsys: pytest.CaptureFixture[str]) -> None:
    assert main([]) == 0
    assert "usage: duckterm" in capsys.readouterr().out


def test_unknown_command_is_rejected(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["does-not-exist"])
    assert exc.value.code != 0


def test_doctor_returns_nonzero_when_a_check_fails(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    import duckterm.doctor as doctor

    monkeypatch.setattr(
        doctor, "run", lambda url, agents: [doctor.Result("fail", "boom", "fix it")]
    )
    assert main(["doctor"]) == 1


def test_doctor_returns_zero_when_all_ok(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    import duckterm.doctor as doctor

    monkeypatch.setattr(doctor, "run", lambda url, agents: [doctor.Result("ok", "fine")])
    assert main(["doctor"]) == 0


def test_restart_starts_a_server_when_none_is_running(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    import duckterm.cli as cli

    # No server on the port -> restart should skip the stop and just serve.
    monkeypatch.setattr(cli, "_duckterm_responds", lambda h, p: False)
    served: dict = {}

    def fake_serve(host: str, port: int, *, recover: bool = False) -> int:
        assert recover
        served["hp"] = (host, port)
        return 0

    monkeypatch.setattr(cli, "_serve", fake_serve)
    assert main(["restart", "--port", "4321"]) == 0
    assert served["hp"][1] == 4321


def test_run_execs_a_heartbeat_wrapped_shell_passing_args_through(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    import duckterm.cli as cli

    monkeypatch.setattr(cli.shutil, "which", lambda a: "/usr/bin/" + a)
    monkeypatch.setattr(cli, "_hook_installed", lambda r: True)
    monkeypatch.setattr(cli, "_duckterm_responds", lambda h, p: True)
    monkeypatch.setattr(cli, "_register_run_session", lambda *a: None)
    execed: dict = {}
    monkeypatch.setattr(cli.os, "execvp", lambda file, argv: execed.update(file=file, argv=argv))

    main(["run", "claude", "--resume", "xyz"])

    # Runs via `sh -c` (so a heartbeat loop + the agent share the terminal), and
    # the agent invocation with its passthrough args is in the wrapped command.
    assert execed["file"] == "sh"
    assert execed["argv"][:2] == ["sh", "-c"]
    assert "claude --resume xyz" in execed["argv"][2]


def test_run_registers_a_launched_session_with_its_name(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    import duckterm.cli as cli

    monkeypatch.setattr(cli.shutil, "which", lambda a: "/usr/bin/" + a)
    monkeypatch.setattr(cli, "_hook_installed", lambda r: True)
    monkeypatch.setattr(cli, "_duckterm_responds", lambda h, p: True)
    monkeypatch.setattr(cli.os, "execvp", lambda *a: None)
    posted: list = []
    patched: list = []
    monkeypatch.setattr(cli, "_post_json", lambda path, body: posted.append((path, body)))
    monkeypatch.setattr(cli, "_patch_json", lambda path, body: patched.append((path, body)))

    main(["run", "--name", "login refactor", "claude"])

    start = next(b for p, b in posted if p == "/events")
    assert start["launched"] is True
    assert start["runtime"] == "claude-code"
    assert patched and patched[0][1]["name"] == "login refactor"


def test_run_warns_when_hooks_missing_then_still_execs(monkeypatch, capsys) -> None:  # type: ignore[no-untyped-def]
    import duckterm.cli as cli

    monkeypatch.setattr(cli.shutil, "which", lambda a: "/usr/bin/" + a)
    monkeypatch.setattr(cli, "_hook_installed", lambda r: False)  # not installed
    monkeypatch.setattr(cli, "_duckterm_responds", lambda h, p: True)
    monkeypatch.setattr(cli, "_register_run_session", lambda *a: None)
    monkeypatch.setattr(cli.os, "execvp", lambda *a: None)

    main(["run", "claude"])

    err = capsys.readouterr().err
    assert "hooks aren't installed" in err
    assert "install-hooks --agent claude-code" in err


def test_run_missing_agent_returns_127(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    import duckterm.cli as cli

    monkeypatch.setattr(cli.shutil, "which", lambda a: None)  # not on PATH
    assert main(["run", "nope"]) == 127


def test_restart_waits_for_old_process_exit_after_http_stops(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    import duckterm.cli as cli
    import duckterm.persistence.history as history

    steps: list[str] = []
    alive = True
    listeners = iter([{42}, set()])
    monkeypatch.setattr(cli, "_duckterm_responds", lambda h, p: True)
    monkeypatch.setattr(cli, "_restart_identity_matches", lambda h, p: True)
    (cli.instance.home() / "server.pid").write_text("42")
    monkeypatch.setattr(cli, "_listening_pids", lambda p: next(listeners))

    def kill(pid: int, signal: int) -> None:
        assert pid == 42
        steps.append("signal")

    def sleep(seconds: float) -> None:
        nonlocal alive
        steps.append("wait")
        alive = False

    class Store:
        def __init__(self) -> None:
            assert not alive  # HTTP could stop before shutdown released SQLite.
            steps.append("open_db")

        def clear_tombstones(self) -> int:
            steps.append("clear")
            return 0

        def close(self) -> None:
            steps.append("close_db")

    monkeypatch.setattr(cli.os, "kill", kill)
    monkeypatch.setattr(cli, "_pid_alive", lambda p: alive)
    monkeypatch.setattr("time.sleep", sleep)
    monkeypatch.setattr(history, "HistoryStore", Store)

    def serve(host, port, *, recover):
        assert not alive
        assert recover
        steps.append("serve")
        return 0

    monkeypatch.setattr(cli, "_serve", serve)
    assert cli._restart("127.0.0.1", 4300) == 0
    assert steps == ["signal", "wait", "serve"]  # recovery waits for serve's bind + home lock


def test_restart_refuses_to_open_db_while_old_process_is_alive(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    import duckterm.cli as cli
    import duckterm.persistence.history as history

    monkeypatch.setattr(cli, "_duckterm_responds", lambda h, p: True)
    monkeypatch.setattr(cli, "_restart_identity_matches", lambda h, p: True)
    (cli.instance.home() / "server.pid").write_text("42")
    monkeypatch.setattr(cli, "_listening_pids", lambda p: {42})
    monkeypatch.setattr(cli.os, "kill", lambda pid, sig: None)
    monkeypatch.setattr(cli, "_pid_alive", lambda pid: True)
    waits: list[float] = []
    monkeypatch.setattr("time.sleep", waits.append)
    monkeypatch.setattr(history, "HistoryStore", lambda: pytest.fail("DB opened too early"))
    monkeypatch.setattr(cli, "_serve", lambda h, p: pytest.fail("server started too early"))
    assert cli._restart("127.0.0.1", 4300) == 1
    assert len(waits) == 50


def test_restart_refuses_second_listener_before_opening_db(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    import duckterm.cli as cli
    import duckterm.persistence.history as history

    listeners = iter([{42}, {88}])
    monkeypatch.setattr(cli, "_duckterm_responds", lambda h, p: True)
    monkeypatch.setattr(cli, "_restart_identity_matches", lambda h, p: True)
    (cli.instance.home() / "server.pid").write_text("42")
    monkeypatch.setattr(cli, "_listening_pids", lambda p: next(listeners))
    monkeypatch.setattr(cli.os, "kill", lambda pid, sig: None)
    monkeypatch.setattr(cli, "_pid_alive", lambda pid: False)
    monkeypatch.setattr(history, "HistoryStore", lambda: pytest.fail("DB opened beside new server"))
    monkeypatch.setattr(cli, "_serve", lambda h, p: pytest.fail("server started beside new server"))
    assert cli._restart("127.0.0.1", 4300) == 1
