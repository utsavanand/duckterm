import asyncio
import os
import sys

import pytest

from duckterm.harnesses import REGISTRY
from duckterm.runtimes import opencode_installation as installation
from duckterm.runtimes import opencode_options
from duckterm.runtimes.opencode import OpenCodeRuntime
from duckterm.runtimes.opencode_protocol import VERSION, OpenCodeError


def executable(tmp_path, body):
    path = tmp_path / "opencode"
    path.write_text(f"#!{sys.executable}\n" + body)
    path.chmod(0o700)
    return str(path)


@pytest.mark.parametrize("output,code", [(VERSION, 0), ("other", 0), (VERSION, 1), ("x" * 512, 0)])
def test_checks_actual_child_version_and_exit_status(tmp_path, output, code):
    binary = executable(tmp_path, f"print({output!r})\nraise SystemExit({code})\n")
    run = installation.verify_binary(binary, str(tmp_path), {})
    if output == VERSION and code == 0:
        asyncio.run(run)
    else:
        with pytest.raises(OpenCodeError):
            asyncio.run(run)


def test_cancel_reaps_version_child_without_blocking_event_loop(tmp_path):
    marker = tmp_path / "pid"
    binary = executable(
        tmp_path,
        f"import os,time\nopen({str(marker)!r},'w').write(str(os.getpid()))\ntime.sleep(30)\n",
    )

    async def run():
        task = asyncio.create_task(installation.verify_binary(binary, str(tmp_path), {}))
        try:
            async with asyncio.timeout(3):
                while not marker.exists() or not marker.read_text():
                    await asyncio.sleep(0.01)
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        with pytest.raises(ProcessLookupError):
            os.kill(int(marker.read_text()), 0)

    asyncio.run(run())


def test_launch_options_require_registration_and_destination_version(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENCODE_TUI_CONFIG", raising=False)
    monkeypatch.delenv("OPENCODE_DB", raising=False)
    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.delitem(REGISTRY, "opencode", raising=False)
    executable(tmp_path, f"print({VERSION!r})\n")
    assert not asyncio.run(opencode_options.launch_options())["launch"]["available"]
    monkeypatch.setitem(REGISTRY, "opencode", OpenCodeRuntime)
    ready = asyncio.run(opencode_options.launch_options())
    assert ready["launch"] == {"available": True}
    assert {c["id"] for c in ready["capabilities"] if c["available"]} == {"launch", "history"}
    executable(tmp_path, "print('unsupported')\n")
    changed = asyncio.run(opencode_options.launch_options())
    assert changed["launch"]["available"] is False
    assert VERSION in changed["launch"]["reason"]
    monkeypatch.setenv("OPENCODE_TUI_CONFIG", "/custom/config")
    assert (
        "OPENCODE_TUI_CONFIG" in asyncio.run(opencode_options.launch_options())["launch"]["reason"]
    )


def test_missing_installation_reports_actionable_reason(tmp_path):
    reason = asyncio.run(installation.readiness({"PATH": str(tmp_path)}))
    assert "Install OpenCode " + VERSION in reason
