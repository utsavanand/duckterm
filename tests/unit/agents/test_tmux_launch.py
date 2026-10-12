"""Large handoffs must arrive intact without exceeding tmux's command limit."""

import json
import shlex
import shutil
import sys
import time

import pytest

from duckterm.agents import tmux


@pytest.mark.skipif(shutil.which("tmux") is None, reason="tmux not installed")
@pytest.mark.parametrize("piped", [False, True])
@pytest.mark.parametrize("explicit", [False, True])
def test_interactive_launch_ignores_inherited_no_color(tmp_path, piped, explicit):
    # An automated launcher can seed its private tmux server with NO_COLOR.
    # Reuse that server: sanitizing only the current Python env is insufficient.
    ok, error = tmux._tmux(
        "start-server",
        ";",
        "set-option",
        "-s",
        "exit-empty",
        "off",
        ";",
        "set-environment",
        "-g",
        "NO_COLOR",
        "1",
    )
    assert ok, error
    result = tmp_path / "color.json"
    script = (
        "import json,os,pathlib,time; "
        f"pathlib.Path({str(result)!r}).write_text(json.dumps(os.environ.get('NO_COLOR'))); "
        "print('PLAIN' if os.environ.get('NO_COLOR') else '\\x1b[32mGREEN\\x1b[0m', flush=True); "
        "time.sleep(30)"
    )
    key = "test-color-environment"
    env = {"NO_COLOR": "1"} if explicit else {}
    command = shlex.join([sys.executable, "-c", script])
    try:
        if piped:
            tmux.spawn_piped(key, command, str(tmp_path), str(tmp_path / "pane.log"), env)
        else:
            tmux.spawn(key, command, str(tmp_path), env)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and not result.exists():
            time.sleep(0.01)
        assert json.loads(result.read_text()) == ("1" if explicit else None)
        deadline = time.monotonic() + 5
        screen = b""
        while time.monotonic() < deadline:
            screen = tmux.capture_screen(tmux.target_for(key))
            if (b"PLAIN" if explicit else b"GREEN") in screen:
                break
            time.sleep(0.01)
        assert (b"PLAIN" if explicit else b"\x1b[32mGREEN") in screen
        assert tmux._tmux("show-environment", "-g", "NO_COLOR")[1].strip() == "NO_COLOR=1"
    finally:
        tmux.kill_session(tmux.target_for(key))
        tmux._tmux("set-environment", "-gu", "NO_COLOR")


@pytest.mark.skipif(shutil.which("tmux") is None, reason="tmux not installed")
def test_piped_launch_preserves_large_literal_handoff(tmp_path):
    cwd = tmp_path / "project with spaces"
    cwd.mkdir()
    received = cwd / "received.json"
    marker = cwd / "must-not-execute"
    # Quoting a real brief amplifies its size on the old nested-shell path.
    payload = (f'Owner\'s note: "quoted" `touch {marker}` $(touch {marker}) 雪\n' * 500)[:32000]
    script = (
        "import json,os,pathlib,sys,time; "
        "pathlib.Path('received.json').write_text(json.dumps("
        "[sys.argv[1],os.getcwd(),os.environ['HANDOFF_TEST']])); "
        "print('HANDOFF_RECEIVED',flush=True); time.sleep(30)"
    )
    command = shlex.join([sys.executable, "-c", script, payload])
    pane = tmp_path / "pane.log"
    target = tmux.target_for("test-large-handoff")
    try:
        tmux.spawn_piped(
            "test-large-handoff", command, str(cwd), str(pane), {"HANDOFF_TEST": "unchanged"}
        )
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if received.exists() and pane.exists() and b"HANDOFF_RECEIVED" in pane.read_bytes():
                break
            time.sleep(0.01)
        assert json.loads(received.read_text()) == [payload, str(cwd), "unchanged"]
        assert b"HANDOFF_RECEIVED" in pane.read_bytes()
        assert not marker.exists()
        assert not list(tmp_path.glob(".launch-*.sh"))
    finally:
        tmux.kill_session(target)


@pytest.mark.parametrize("failure", ["spawn", "pipe-pane", "wait-for"])
def test_failed_launch_removes_private_command_and_never_runs_it(tmp_path, monkeypatch, failure):
    command = "printf 'PRIVATE HANDOFF'"
    killed = []

    def spawn(session_id, guarded, cwd, env):
        scripts = list(tmp_path.glob(".launch-*.sh"))
        assert len(scripts) == 1
        assert scripts[0].stat().st_mode & 0o777 == 0o600
        assert command in scripts[0].read_text()
        assert "PRIVATE HANDOFF" not in guarded
        if failure == "spawn":
            raise ValueError("synthetic launch failure")
        return "rd_private-launch"

    monkeypatch.setattr(tmux, "spawn", spawn)
    monkeypatch.setattr(tmux, "kill_session", lambda target: killed.append(target))
    monkeypatch.setattr(tmux, "_tmux", lambda *args: (args[0] != failure, "synthetic failure"))
    with pytest.raises(ValueError):
        tmux.spawn_piped("private-launch", command, str(tmp_path), str(tmp_path / "pane.log"))
    assert not list(tmp_path.glob(".launch-*.sh"))
    assert killed == ([] if failure == "spawn" else ["rd_private-launch"])
