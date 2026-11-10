"""Native setup fixtures: no real installer, account, network, tmux or sessions."""

import base64
import hashlib
import json
import os
import signal
import stat
import subprocess
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from duckterm.runtimes import setup, setup_process


@pytest.fixture
def environment(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    binaries = tmp_path / "bin"
    binaries.mkdir()
    return {"HOME": str(home), "PATH": str(binaries) + os.pathsep + os.defpath}


def binary(environment, name, body):
    path = Path(environment["PATH"].split(os.pathsep)[0]) / name
    path.write_text(f"#!{sys.executable}\n{body}\n")
    path.chmod(0o700)
    return path


def invoke(environment, runtime="codex", action="status", **kwargs):
    events = []
    setup.execute(
        runtime,
        action,
        environment=environment,
        emit=events.append,
        control=setup_process.Control(),
        **kwargs,
    )
    return events


def dead(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    # A killed grandchild may briefly remain an orphan zombie until launchd/init
    # reaps it. It cannot execute or keep a terminal open.
    result = subprocess.run(
        ["/bin/ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True
    )
    return not result.stdout.strip() or result.stdout.lstrip().startswith("Z")


def wait_file(path):
    deadline = time.monotonic() + 3
    while not path.exists() or not path.read_text():
        if time.monotonic() >= deadline:
            raise AssertionError("Fixture child did not start")
        time.sleep(0.01)


def test_missing_and_environment_key_are_not_account_authentication(environment):
    result = invoke({**environment, "OPENAI_API_KEY": "SECRET"})[0]
    assert result["installed"] is False
    assert result["authentication"] == "not_checked"
    assert result["environment_api_key_configured"] is True
    assert "SECRET" not in json.dumps(result)


@pytest.mark.parametrize(
    "runtime,name,body,authentication,method",
    [
        ("codex", "codex", "print('Logged in using ChatGPT')", "signed_in", "account"),
        ("codex", "codex", "print('Logged in using an API key - SECRET');", "signed_in", "api_key"),
        ("codex", "codex", "print('Not logged in'); raise SystemExit(1)", "signed_out", "unknown"),
        (
            "codex",
            "codex",
            "print('SECRET bad configuration'); raise SystemExit(1)",
            "unknown",
            "unknown",
        ),
        (
            "claude-code",
            "claude",
            'print(\'{"loggedIn":true,"authMethod":"claude.ai","email":"SECRET"}\')',
            "signed_in",
            "account",
        ),
        (
            "claude-code",
            "claude",
            'print(\'{"loggedIn":true,"authMethod":"api_key"}\')',
            "signed_in",
            "api_key",
        ),
        (
            "claude-code",
            "claude",
            "print('{\"loggedIn\":false}'); raise SystemExit(1)",
            "signed_out",
            "unknown",
        ),
        (
            "claude-code",
            "claude",
            'print(\'{"loggedIn":"true","email":"SECRET"}\')',
            "unknown",
            "unknown",
        ),
        (
            "claude-code",
            "claude",
            "print('{\"loggedIn\":true}'); raise SystemExit(1)",
            "unknown",
            "unknown",
        ),
    ],
)
def test_status_sanitizes_account_schema_and_never_checks_quota(
    environment, runtime, name, body, authentication, method
):
    expected = ["auth", "status"] if runtime == "claude-code" else ["login", "status"]
    binary(
        environment,
        name,
        f"import sys\nassert sys.argv[1:] in (['--version'], {expected!r})\n"
        f"if sys.argv[1:] == ['--version']:\n print('1.2.3'); raise SystemExit(0)\n{body}",
    )
    result = invoke(environment, runtime)[0]
    assert result["version"] == "1.2.3" and result["probe"] == "ok"
    assert result["authentication"] == authentication
    assert result["credential_method"] == method
    assert "SECRET" not in json.dumps(result)
    assert str(Path(environment["HOME"]).parent) not in json.dumps(result)


def test_real_auth_profile_but_private_version_home_and_no_enrollment(environment, tmp_path):
    record = tmp_path / "observed.jsonl"
    auth_home = tmp_path / "existing-codex"
    auth_home.mkdir()
    sentinel = auth_home / "auth.json"
    sentinel.write_text("unchanged fixture credential")
    binary(
        environment,
        "codex",
        f"""import json,os,sys
from pathlib import Path
with open({str(record)!r}, 'a') as out:
 out.write(json.dumps({{'argv':sys.argv[1:],'env':dict(os.environ),'cwd':os.getcwd()}})+'\\n')
if sys.argv[1:] == ['--version']:
 Path.home().joinpath('version-cache').write_text('private')
 print('1.2.3')
else:
 contents = Path(os.environ['CODEX_HOME']).joinpath('auth.json').read_text()
 assert contents == 'unchanged fixture credential'
 print('Logged in using ChatGPT')
""",
    )
    env = {
        **environment,
        "CODEX_HOME": str(auth_home),
        "OPENAI_BASE_URL": "https://fixture.invalid",
        "OPENAI_API_KEY": "fixture-agent-key",
        "DUCKTERM_SESSION_KEY": "SECRET_SESSION",
        "DUCKTERM_SESSION_TOKEN_FILE": "SECRET_TOKEN",
        "DUCKTERM_HOME": "SECRET_PROFILE",
        "CLAUDECODE": "SECRET_AGENT",
        "CODEX_THREAD_ID": "SECRET_THREAD",
        "NODE_OPTIONS": "--require=SECRET",
        "PYTHONPATH": "SECRET",
        "BASH_ENV": "SECRET",
        "TMUX": "SECRET",
    }
    assert invoke(env)[0]["authentication"] == "signed_in"
    version, auth = [json.loads(line) for line in record.read_text().splitlines()]
    assert version["env"]["HOME"] != environment["HOME"]
    assert not Path(version["env"]["HOME"]).exists()
    assert "CODEX_HOME" not in version["env"]
    assert auth["env"]["HOME"] == environment["HOME"]
    assert auth["env"]["CODEX_HOME"] == str(auth_home)
    assert auth["env"]["OPENAI_API_KEY"] == "fixture-agent-key"
    assert auth["env"]["OPENAI_BASE_URL"] == "https://fixture.invalid"
    assert auth["env"]["DUCKTERM_INTERNAL"] == "1"
    assert "SECRET" not in json.dumps(auth)
    assert auth["cwd"] != environment["HOME"] and not Path(auth["cwd"]).exists()
    assert sentinel.read_text() == "unchanged fixture credential"
    assert not list(Path(environment["HOME"]).rglob("db.sqlite"))
    assert not list(Path(environment["HOME"]).rglob("*.log"))


def test_unrecognized_version_does_not_hide_valid_auth(environment):
    binary(
        environment,
        "codex",
        "import sys\n"
        "print('future-version SECRET' if '--version' in sys.argv else 'Logged in using ChatGPT')",
    )
    result = invoke(environment)[0]
    assert result["probe"] == "unrecognized"
    assert result["authentication"] == "signed_in"


@pytest.mark.parametrize("body", ["print('SECRET' * 10000)", "import time; time.sleep(10)"])
def test_auth_output_limit_and_timeout_are_sanitized(environment, monkeypatch, body):
    monkeypatch.setattr(setup, "AUTH_TIMEOUT", 0.1)
    binary(
        environment,
        "codex",
        "import sys\nif '--version' in sys.argv:\n print('1.2.3'); raise SystemExit(0)\n" + body,
    )
    result = invoke(environment)[0]
    assert result["authentication"] == "unknown"
    assert result["reason"] == "auth_status_unavailable"
    assert "SECRET" not in json.dumps(result)


def test_fixed_install_actions_and_no_provider_keys_reach_installer(environment, monkeypatch):
    commands = []

    def fake_run(argv, env, cwd, **options):
        assert options["interactive"] is True and options["timeout"] == 600
        commands.append((argv, dict(env)))
        return 0, b""

    monkeypatch.setattr(setup, "run_command", fake_run)
    env = {
        **environment,
        "OPENAI_API_KEY": "SECRET",
        "CLAUDE_CODE_OAUTH_TOKEN": "SECRET",
        "CODEX_HOME": "SECRET",
    }
    for runtime, url, interpreter in [
        ("claude-code", "https://claude.ai/install.sh", "/bin/bash"),
        ("codex", "https://chatgpt.com/codex/install.sh", "/bin/sh"),
    ]:
        assert invoke(env, runtime, "install")[-1]["exit_code"] == 0
        argv, child_env = commands[-1]
        assert argv[:6] == ["/bin/bash", "--noprofile", "--norc", "-o", "pipefail", "-c"]
        assert argv[-1].endswith(url + " | " + interpreter)
        assert argv[-1].startswith("/usr/bin/curl -q --proto '=https'")
        assert "SECRET" not in json.dumps(child_env)


@pytest.mark.parametrize(
    "runtime,action,selected",
    [
        ("evil", "install", None),
        ("codex", "shell", None),
        ("codex", "install", "/arbitrary"),
        ("codex", "login", "relative"),
        ("codex", "login", "/nonexistent/SECRET"),
    ],
)
def test_invalid_actions_and_paths_do_not_spawn(
    environment, monkeypatch, runtime, action, selected
):
    monkeypatch.setattr(setup, "run_command", lambda *a, **k: pytest.fail("unexpected spawn"))
    with pytest.raises(setup.SetupFailure) as failure:
        invoke(environment, runtime, action, selected=selected)
    assert str(failure.value) in {"invalid_request", "invalid_executable"}
    assert "SECRET" not in str(failure.value)


def test_native_selected_executable_does_not_rewrite_other_launch_config(environment):
    binary(environment, "codex", "raise SystemExit('wrong executable')")
    selected = binary(
        environment,
        "custom-codex",
        "import sys\nprint('1.2.3' if '--version' in sys.argv else 'Logged in using ChatGPT')",
    )
    env_before = dict(environment)
    assert invoke(environment, selected=str(selected))[0]["authentication"] == "signed_in"
    assert environment == env_before
    selected.chmod(0o600)
    with pytest.raises(setup.SetupFailure, match="invalid_executable"):
        invoke(environment, selected=str(selected))


def test_lock_shared_across_profiles_and_released_on_error(environment):
    with setup.runtime_lock("codex", environment["HOME"]):
        with pytest.raises(setup.SetupFailure, match="busy"):
            invoke({**environment, "DUCKTERM_HOME": "/a-different-edition"})
        with setup.runtime_lock("claude-code", environment["HOME"]):
            pass
    assert invoke(environment)[0]["installed"] is False
    lock_root = Path(environment["HOME"]) / ".duckterm-runtime-setup"
    assert stat.S_IMODE(lock_root.stat().st_mode) == 0o700
    assert all(
        p.read_bytes() == b"" and stat.S_IMODE(p.stat().st_mode) == 0o600
        for p in lock_root.iterdir()
    )


@pytest.mark.parametrize("target", ["directory", "file", "hardlink", "permissions"])
def test_lock_refuses_links_and_unsafe_permissions(environment, tmp_path, target):
    root = Path(environment["HOME"]) / ".duckterm-runtime-setup"
    outside = tmp_path / "outside"
    outside.mkdir()
    if target == "directory":
        root.symlink_to(outside, target_is_directory=True)
    else:
        root.mkdir(mode=0o700)
        lock = root / (hashlib.sha256(b"codex").hexdigest() + ".lock")
        sentinel = outside / "untouched"
        sentinel.write_text("unchanged")
        sentinel.chmod(0o600)
        if target == "file":
            lock.symlink_to(sentinel)
        elif target == "hardlink":
            os.link(sentinel, lock)
        else:
            root.chmod(0o755)
    with pytest.raises((OSError, setup.SetupFailure)):
        invoke(environment)
    if (outside / "untouched").exists():
        assert (outside / "untouched").read_text() == "unchanged"


def test_symlinked_trusted_home_is_accepted(environment, tmp_path):
    alias = tmp_path / "home-alias"
    alias.symlink_to(environment["HOME"], target_is_directory=True)
    assert invoke({**environment, "HOME": str(alias)})[0]["installed"] is False


def test_login_has_controlling_tty_fixed_argv_and_bounded_io(environment, tmp_path):
    marker = tmp_path / "tty.json"
    binary(
        environment,
        "codex",
        f"""import fcntl,json,os,struct,sys,termios
assert sys.argv[1:] == ['login']
assert all(os.isatty(fd) for fd in (0,1,2))
with open('/dev/tty','rb', buffering=0): pass
print('READY', flush=True)
line = input()
size = struct.unpack('HHHH', fcntl.ioctl(0,termios.TIOCGWINSZ,bytes(8)))
open({str(marker)!r},'w').write(json.dumps({{'line':line,'rows':size[0],'cols':size[1]}}))
print('DONE', flush=True)
""",
    )
    incoming, outgoing = os.pipe()
    events = []

    def emit(event):
        events.append(event)
        if event["type"] == "ready":
            frames = [
                {"type": "resize", "rows": 40, "cols": 120},
                {"type": "input", "data": base64.b64encode(b"fixture answer\n").decode()},
            ]
            os.write(outgoing, b"".join(json.dumps(frame).encode() + b"\n" for frame in frames))

    try:
        setup.execute(
            "codex",
            "login",
            environment=environment,
            emit=emit,
            control=setup_process.Control(incoming),
        )
    finally:
        os.close(incoming)
        os.close(outgoing)
    assert json.loads(marker.read_text()) == {"line": "fixture answer", "rows": 40, "cols": 120}
    assert events[-1] == {"type": "exited", "exit_code": 0, "cancelled": False}
    output = b"".join(base64.b64decode(e["data"]) for e in events if e["type"] == "output")
    assert b"DONE" in output


@pytest.mark.parametrize("mode", ["cancel", "eof", "timeout", "leader_exit"])
def test_owned_descendants_are_stopped_before_leader_is_reaped(
    environment, tmp_path, monkeypatch, mode
):
    marker = tmp_path / "pids"
    binary(
        environment,
        "codex",
        f"""import os,signal,time
signal.signal(signal.SIGTERM,signal.SIG_IGN)
child=os.fork()
if child == 0:
 while True: time.sleep(1)
open({str(marker)!r},'w').write(str(os.getpid())+' '+str(child))
if {mode!r} == 'leader_exit': raise SystemExit(0)
while True: time.sleep(1)
""",
    )
    incoming, outgoing = os.pipe()
    cancelled = threading.Event()
    clock_started = threading.Event()
    expired = threading.Event()
    if mode == "timeout":
        monkeypatch.setattr(setup, "INTERACTIVE_TIMEOUT", 0.2)

        def monotonic():
            value = 1.0 if expired.is_set() else 0.0
            clock_started.set()
            return value

        # Start the timeout only after both fixture processes are ready. A
        # real 200ms deadline can kill the Python PTY wrapper before it execs
        # this fixture, which tests startup speed instead of group cleanup.
        monkeypatch.setattr(
            setup_process, "time", SimpleNamespace(monotonic=monotonic, sleep=time.sleep)
        )
    errors = []

    def work():
        try:
            setup.execute(
                "codex",
                "login",
                environment=environment,
                emit=lambda _: None,
                control=setup_process.Control(incoming, cancelled),
            )
        except Exception as exc:
            errors.append(exc)

    worker = threading.Thread(target=work)
    worker.start()
    try:
        wait_file(marker)
        pids = [int(value) for value in marker.read_text().split()]
        if mode == "cancel":
            cancelled.set()
        elif mode == "eof":
            os.close(outgoing)
            outgoing = None
        elif mode == "timeout":
            assert clock_started.wait(timeout=3)
            assert worker.is_alive() and not any(dead(pid) for pid in pids)
            expired.set()
        worker.join(timeout=4)
        assert not worker.is_alive()
        if mode == "leader_exit":
            assert errors == []
        else:
            assert len(errors) == 1 and isinstance(errors[0], setup.SetupFailure)
            assert str(errors[0]) == ("timeout" if mode == "timeout" else "cancelled")
        assert all(dead(pid) for pid in pids)
        with pytest.raises(ChildProcessError):
            os.waitpid(pids[0], os.WNOHANG)
    finally:
        cancelled.set()
        worker.join(timeout=4)
        os.close(incoming)
        if outgoing is not None:
            os.close(outgoing)


@pytest.mark.parametrize(
    "frame",
    [
        b"{}\n",
        b"[]\n",
        b'{"type":"shell","command":"SECRET"}\n',
        b'{"type":"input","data":"%%%"}\n',
        b'{"type":"cancel","type":"cancel"}\n',
        b'{"type":"resize","cols":true,"rows":20}\n',
        b'{"type":"resize","cols":501,"rows":20}\n',
        b"[" * 1000 + b"]" * 1000 + b"\n",
        b"x" * 65537,
    ],
)
def test_malformed_protocol_is_bounded_and_cannot_execute(frame):
    read_fd, write_fd = os.pipe()
    master, slave = os.openpty()
    writer = threading.Thread(target=lambda: _write_all(write_fd, frame))
    writer.start()
    try:
        control = setup_process.Control(read_fd)
        with pytest.raises(setup.SetupFailure, match="invalid_command"):
            for _ in range(100):
                control.poll(master)
                time.sleep(0.001)
    finally:
        os.close(read_fd)
        writer.join(timeout=2)
        os.close(write_fd)
        os.close(master)
        os.close(slave)


def _write_all(fd, data):
    try:
        while data:
            data = data[os.write(fd, data) :]
    except BrokenPipeError:
        pass


def test_partial_control_waits_for_newline_and_eof_cancels():
    read_fd, write_fd = os.pipe()
    try:
        os.write(write_fd, b'{"type":"can')
        control = setup_process.Control(read_fd)
        control.poll()
        os.write(write_fd, b'cel"}\n')
        with pytest.raises(setup.SetupFailure, match="cancelled"):
            control.poll()
        os.close(write_fd)
        write_fd = None
        with pytest.raises(setup.SetupFailure, match="cancelled"):
            setup_process.Control(read_fd).poll()
    finally:
        os.close(read_fd)
        if write_fd is not None:
            os.close(write_fd)


def helper(environment, arguments):
    source = str(Path(setup.__file__).parents[2])
    program = (
        "import sys;sys.path.insert(0,"
        + repr(source)
        + ");from duckterm.runtimes.setup import main;raise SystemExit(main())"
    )
    return subprocess.Popen(
        [sys.executable, "-I", "-B", "-c", program, *arguments],
        env=environment,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def test_entrypoint_rejects_arbitrary_fields_without_echoing_secrets(environment):
    child = helper(environment, ["--command", "SECRET_COMMAND"])
    output, error = child.communicate(timeout=3)
    assert child.returncode == 1 and error == b""
    assert json.loads(output) == {"protocol": 1, "type": "error", "reason": "invalid_request"}


def test_protocol_sigterm_reaps_interactive_child_and_no_raw_error(environment, tmp_path):
    marker = tmp_path / "pid"
    binary(
        environment,
        "codex",
        f"import os,time\nopen({str(marker)!r},'w').write(str(os.getpid()))\n"
        "while True:time.sleep(1)",
    )
    child = helper(environment, ["--runtime", "codex", "--action", "login"])
    try:
        wait_file(marker)
        child.send_signal(signal.SIGTERM)
        output, error = child.communicate(timeout=3)
        events = [json.loads(line) for line in output.splitlines()]
        assert child.returncode == 1 and error == b""
        assert events[-1] == {"protocol": 1, "type": "error", "reason": "cancelled"}
        assert dead(int(marker.read_text()))
    finally:
        if child.poll() is None:
            child.kill()
            child.wait()


def test_stalled_transport_fails_without_unbounded_buffer():
    read_fd, write_fd = os.pipe()
    try:
        writer = setup_process.ProtocolWriter(write_fd)
        data = {"type": "output", "data": "x" * 60000}
        start = time.monotonic()
        with pytest.raises(setup.SetupFailure, match="transport_unavailable"):
            while time.monotonic() - start < 3:
                writer(data)
        assert time.monotonic() - start < 2.5
    finally:
        os.close(read_fd)
        os.close(write_fd)


@pytest.mark.parametrize("state", [b"S\n", b"Z\nS\n", b"?\n"])
def test_darwin_eperm_never_hides_live_or_unknown_group_members(monkeypatch, state):
    def denied(*args):
        raise PermissionError("SECRET")

    monkeypatch.setattr(setup_process.sys, "platform", "darwin")
    monkeypatch.setattr(setup_process.os, "killpg", denied)
    monkeypatch.setattr(setup_process, "_exited", lambda _: True)
    monkeypatch.setattr(
        setup_process.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=0, stdout=state)
    )
    with pytest.raises(setup.SetupFailure, match="^cleanup_failed$"):
        setup_process._kill_group(SimpleNamespace(pid=123), signal.SIGKILL)


def test_live_leader_signal_denial_is_never_treated_as_zombie(monkeypatch):
    def denied(*args):
        raise PermissionError("SECRET")

    monkeypatch.setattr(setup_process.os, "killpg", denied)
    monkeypatch.setattr(setup_process, "_exited", lambda _: False)
    monkeypatch.setattr(
        setup_process.subprocess, "run", lambda *a, **k: pytest.fail("no ps needed")
    )
    with pytest.raises(setup.SetupFailure, match="^cleanup_failed$"):
        setup_process._kill_group(SimpleNamespace(pid=123), signal.SIGKILL)


def test_status_eof_cleans_up_without_emitting_captured_auth_output(environment, tmp_path):
    marker = tmp_path / "auth-pid"
    binary(
        environment,
        "codex",
        f"""import os,sys,time
if sys.argv[1:] == ['--version']:
 print('1.2.3'); raise SystemExit(0)
open({str(marker)!r},'w').write(str(os.getpid()))
print('SECRET_ACCOUNT_TOKEN',flush=True)
while True: time.sleep(1)
""",
    )
    child = helper(environment, ["--runtime", "codex", "--action", "status"])
    try:
        wait_file(marker)
        output, error = child.communicate(timeout=3)  # EOF from native cancellation.
        assert error == b"" and b"SECRET" not in output
        assert json.loads(output) == {"protocol": 1, "type": "error", "reason": "cancelled"}
        assert dead(int(marker.read_text()))
    finally:
        if child.poll() is None:
            child.kill()
            child.wait()


def test_stdout_backpressure_cancels_running_interactive_group(environment, tmp_path):
    marker = tmp_path / "pid"
    binary(
        environment,
        "codex",
        f"""import os
open({str(marker)!r},'w').write(str(os.getpid()))
while True: os.write(1,b'x'*4096)
""",
    )
    child = helper(environment, ["--runtime", "codex", "--action", "login"])
    try:
        wait_file(marker)
        child.wait(timeout=4)  # Deliberately do not drain stdout.
        assert child.returncode == 1
        assert dead(int(marker.read_text()))
        assert child.stderr.read() == b""
    finally:
        if child.poll() is None:
            child.kill()
            child.wait()
        for stream in (child.stdin, child.stdout, child.stderr):
            stream.close()


def test_auth_json_duplicate_keys_fail_closed():
    result = setup.auth_result("claude-code", 0, b'{"loggedIn":false,"loggedIn":true}')
    assert result["authentication"] == "unknown"


def test_failed_version_cleanup_never_starts_authentication(environment, monkeypatch):
    binary(environment, "codex", "raise SystemExit(0)")
    calls = []

    def fail(argv, *args, **kwargs):
        calls.append(argv)
        raise setup.SetupFailure("cleanup_failed")

    monkeypatch.setattr(setup, "run_command", fail)
    with pytest.raises(setup.SetupFailure, match="^cleanup_failed$"):
        invoke(environment)
    assert len(calls) == 1 and calls[0][1:] == ["--version"]
