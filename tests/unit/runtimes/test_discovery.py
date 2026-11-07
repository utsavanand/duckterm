"""Discovery executes only bounded version probes, never provider authentication."""

import asyncio
import json
import os
import sys
from pathlib import Path

import pytest

from duckterm.harnesses import REGISTRY
from duckterm.runtimes import discovery
from duckterm.runtimes.opencode_protocol import VERSION


def binary(tmp_path, name, script):
    path = tmp_path / name
    path.write_text(f"#!{sys.executable}\n" + script)
    path.chmod(0o700)
    return path


def inspect(tmp_path, runtime="codex", command="codex", **environment):
    return asyncio.run(
        discovery.inspect_runtime(runtime, runtime, command, {"PATH": str(tmp_path), **environment})
    )


def test_missing_binary_does_not_probe_or_claim_authentication(tmp_path, monkeypatch):
    async def forbidden(*args, **kwargs):
        raise AssertionError("No child may start for a missing binary")

    monkeypatch.setattr(discovery, "version_output", forbidden)
    response = asyncio.run(discovery.discover({"PATH": str(tmp_path)}))
    assert response["version"] == 1
    assert len(response["runtimes"]) == 4
    for row in response["runtimes"]:
        assert not row["installed"] and row["probe"] == "missing"
        assert row["version"] is None and row["support"] == "unavailable"
        assert row["authentication"] == "not_checked"


@pytest.mark.parametrize(
    "runtime,command,output,version",
    [
        ("claude-code", "claude", "2.1.3 (Claude Code)", "2.1.3"),
        ("codex", "codex", "codex-cli 0.159.3", "0.159.3"),
        ("copilot", "copilot", "GitHub Copilot CLI 1.0.62\nCommit: private-extra-output", "1.0.62"),
        ("opencode", "opencode", VERSION, VERSION),
        (
            "copilot",
            "copilot",
            "GitHub Copilot CLI 1.0.90.\nRun 'copilot update' to check for updates.",
            "1.0.90",
        ),
    ],
)
def test_installed_versions_are_sanitized_without_becoming_login_claims(
    tmp_path, runtime, command, output, version
):
    binary(tmp_path, command, f"print({output!r})\n")
    result = inspect(tmp_path, runtime, command)
    assert result["installed"] and result["probe"] == "ok"
    assert result["version"] == version and result["support"] == "available"
    assert result["authentication"] == "not_checked"
    assert "private-extra-output" not in json.dumps(result)
    assert str(tmp_path) not in json.dumps(result)


def test_opencode_version_and_configuration_follow_existing_launch_contract(tmp_path):
    binary(tmp_path, "opencode", "print('0.0.1')\n")
    old = inspect(tmp_path, "opencode", "opencode")
    assert old["installed"] and old["version"] == "0.0.1"
    assert old["support_reason"] == "unsupported_version"
    binary(tmp_path, "opencode", f"print({VERSION!r})\n")
    for env in ({"OPENCODE_TUI_CONFIG": "/private/config"}, {"OPENCODE_DB": ":memory:"}):
        result = inspect(tmp_path, "opencode", "opencode", **env)
        assert result["support_reason"] == "unsupported_configuration"
        assert "/private/config" not in json.dumps(result)


def test_binary_presence_cannot_enable_an_unregistered_adapter(tmp_path, monkeypatch):
    binary(tmp_path, "opencode", f"print({VERSION!r})\n")
    monkeypatch.delitem(REGISTRY, "opencode")
    result = inspect(tmp_path, "opencode", "opencode")
    assert result["installed"] and result["probe"] == "ok"
    assert result["support"] == "unavailable"
    assert result["support_reason"] == "adapter_unavailable"


@pytest.mark.parametrize(
    "script,probe",
    [
        (
            "import sys\nprint('SECRET_STDOUT')\n"
            "print('SECRET_STDERR', file=sys.stderr)\nraise SystemExit(1)\n",
            "failed",
        ),
        ("print('SECRET_KEY 1.2.3')\n", "unrecognized"),
        ("print('x' * 10000)\n", "unrecognized"),
        ("import os\nos.write(1, b'\\xff\\xfe')\n", "unrecognized"),
    ],
)
def test_bad_version_output_is_bounded_and_never_returned(tmp_path, script, probe):
    binary(tmp_path, "codex", script)
    result = inspect(tmp_path)
    assert result["installed"] and result["probe"] == probe
    assert result["version"] is None and result["support"] == "unknown"
    assert "SECRET" not in json.dumps(result)


def test_spawn_error_does_not_leak_paths_or_mark_binary_missing(tmp_path, monkeypatch):
    binary(tmp_path, "codex", "print('1.2.3')\n")

    def failure(*args, **kwargs):
        raise PermissionError("SECRET_PATH/private/key")

    monkeypatch.setattr(discovery.subprocess, "Popen", failure)
    result = inspect(tmp_path)
    assert result["installed"] and result["probe"] == "failed"
    assert "SECRET" not in json.dumps(result)


def test_only_version_flag_and_private_configuration_reach_child(tmp_path):
    report = tmp_path / "report.json"
    owner_config = tmp_path / "owner"
    owner_config.mkdir()
    sentinel = owner_config / "credential"
    sentinel.write_text("unchanged")
    binary(
        tmp_path,
        "codex",
        f"""import os, sys, json
from pathlib import Path
assert sys.argv[1:] == ['--version']
Path({str(report)!r}).write_text(json.dumps({{'env':dict(os.environ),'cwd':os.getcwd()}}))
Path.home().joinpath('version-cache').write_text('private probe cache')
print('codex-cli 1.2.3')
""",
    )
    result = inspect(
        tmp_path,
        HOME=str(owner_config),
        OPENAI_API_KEY="SECRET",
        ANTHROPIC_API_KEY="SECRET",
        CLAUDE_CONFIG_DIR=str(owner_config),
        NODE_OPTIONS="--require=SECRET",
        PYTHONPATH="SECRET",
        DUCKTERM_TOKEN="SECRET",
    )
    assert result["probe"] == "ok" and result["authentication"] == "not_checked"
    record = json.loads(report.read_text())
    assert Path(record["cwd"]).resolve() == Path(record["env"]["HOME"]).resolve()
    assert record["env"]["PATH"] == str(tmp_path)
    assert "SECRET" not in json.dumps(record)
    assert str(owner_config) not in json.dumps(record)
    assert not Path(record["cwd"]).exists()
    assert sentinel.read_text() == "unchanged"
    assert not (owner_config / "version-cache").exists()


@pytest.mark.parametrize("cancel", [False, True])
def test_timeout_and_cancellation_reap_owned_child(tmp_path, monkeypatch, cancel):
    marker = tmp_path / "pid"
    binary(
        tmp_path,
        "codex",
        f"import os,time\nopen({str(marker)!r},'w').write(str(os.getpid()))\ntime.sleep(30)\n",
    )
    monkeypatch.setattr(discovery, "PROBE_TIMEOUT", 0.3 if not cancel else 4)

    async def run():
        task = asyncio.create_task(
            discovery.inspect_runtime("codex", "Codex", "codex", {"PATH": str(tmp_path)})
        )
        async with asyncio.timeout(2):
            while not marker.exists() or not marker.read_text():
                await asyncio.sleep(0.01)
        if cancel:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            result = await task
            assert result["installed"] and result["probe"] == "timeout"
            assert result["support"] == "unknown"
        with pytest.raises(ProcessLookupError):
            os.kill(int(marker.read_text()), 0)

    asyncio.run(run())


def test_parallel_discovery_is_bounded_and_does_not_block_event_loop(monkeypatch):
    active = peak = 0

    async def probe(runtime, label, command, environment):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.01)
        active -= 1
        return {"id": runtime}

    monkeypatch.setattr(discovery, "inspect_runtime", probe)
    response = asyncio.run(discovery.discover({"PATH": "/synthetic"}))
    assert peak == discovery.PARALLEL_PROBES == 2
    assert [row["id"] for row in response["runtimes"]] == [
        "claude-code",
        "codex",
        "copilot",
        "opencode",
    ]


def test_wrapper_exit_cannot_leave_a_descendant_holding_probe_open(tmp_path, monkeypatch):
    marker = tmp_path / "leader"
    binary(
        tmp_path,
        "codex",
        f"""import os,subprocess,sys
open({str(marker)!r},'w').write(str(os.getpid()))
subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])
""",
    )
    monkeypatch.setattr(discovery, "PROBE_TIMEOUT", 0.3)
    killed = []
    original_killpg = os.killpg

    def stop_owned_group(pid, sig):
        killed.append(pid)
        return original_killpg(pid, sig)

    monkeypatch.setattr(discovery.os, "killpg", stop_owned_group)
    result = inspect(tmp_path)
    assert result["installed"] and result["probe"] == "timeout"
    assert killed == [int(marker.read_text())]
    with pytest.raises(ProcessLookupError):
        os.kill(killed[0], 0)


def test_opencode_extra_output_does_not_claim_existing_exact_probe_would_pass():
    assert discovery.parsed_version("opencode", (VERSION + "\nextra\n").encode()) is None
