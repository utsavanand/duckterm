"""Exercise the native watchdog outside pytest's own faulthandler timer."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

from duckterm.stall_diagnostics import MAX_LOG_BYTES


def run_probe(tmp_path: Path, body: str) -> str:
    script = tmp_path / "probe.py"
    script.write_text(
        "import asyncio, sqlite3, time\n"
        "from pathlib import Path\n"
        "from duckterm.stall_diagnostics import watch_stalls\n"
        f"log = Path({str(tmp_path / 'server-stalls.log')!r})\n" + body
    )
    env = {**os.environ, "PYTHONPATH": str(Path(__file__).parents[2] / "src")}
    result = subprocess.run(
        [sys.executable, str(script)], capture_output=True, text=True, env=env, timeout=15
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stderr


def test_blocking_sql_captures_caller_and_rearms_after_recovery(tmp_path: Path) -> None:
    run_probe(
        tmp_path,
        """
def blocked_database_call():
    secret = 'PRIVATE_PAYLOAD_MUST_NOT_APPEAR'
    db = sqlite3.connect(':memory:')
    db.create_function('slow', 0, lambda: time.sleep(0.8))
    db.execute('select slow()').fetchone()
    db.close()

async def main():
    with watch_stalls(log, timeout=0.5):
        blocked_database_call()
        await asyncio.sleep(0.05)
        blocked_database_call()
        await asyncio.sleep(0.05)
    before = log.read_bytes()
    time.sleep(0.8)
    assert log.read_bytes() == before, 'timer survived shutdown'

asyncio.run(main())
""",
    )
    text = (tmp_path / "server-stalls.log").read_text()
    assert text.count("Timeout (") == 2
    assert "blocked_database_call" in text
    assert "event loop resumed" in text
    assert "PRIVATE_PAYLOAD_MUST_NOT_APPEAR" not in text
    assert (tmp_path / "server-stalls.log").stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize("exception", ["RuntimeError", "asyncio.CancelledError"])
def test_healthy_waits_and_exception_cleanup_do_not_dump(tmp_path: Path, exception: str) -> None:
    run_probe(
        tmp_path,
        f"""
async def main():
    try:
        with watch_stalls(log, timeout=0.5):
            await asyncio.sleep(1.2)
            await asyncio.to_thread(time.sleep, 0.8)
            raise {exception}('exit probe')
    except {exception}:
        pass
    time.sleep(0.8)

asyncio.run(main())
""",
    )
    assert "Timeout (" not in (tmp_path / "server-stalls.log").read_text()


def test_native_timer_failure_does_not_prevent_serving(tmp_path: Path) -> None:
    stderr = run_probe(
        tmp_path,
        """
import faulthandler
def unavailable(*args, **kwargs):
    raise RuntimeError('watchdog thread unavailable')
faulthandler.dump_traceback_later = unavailable
async def main():
    with watch_stalls(log, timeout=0.5):
        await asyncio.sleep(0)
asyncio.run(main())
""",
    )
    assert "diagnostics unavailable" in stderr


def test_repeated_stalls_rotate_and_preserve_crash_handler(tmp_path: Path) -> None:
    run_probe(
        tmp_path,
        """
import faulthandler
import duckterm.stall_diagnostics as diagnostics
diagnostics.MAX_LOG_BYTES = 500
faulthandler.enable()
async def main():
    with watch_stalls(log, timeout=0.5):
        for _ in range(3):
            time.sleep(0.8)
            await asyncio.sleep(0.05)
        assert faulthandler.is_enabled()
    assert faulthandler.is_enabled()
asyncio.run(main())
""",
    )
    previous = (tmp_path / "server-stalls.previous.log").read_text()
    assert previous.count("Timeout (") == 1
    assert "event loop resumed" in (tmp_path / "server-stalls.log").read_text()
    assert len(list(tmp_path.glob("server-stalls*"))) == 2


def test_previous_large_log_is_bounded_and_private(tmp_path: Path) -> None:
    log = tmp_path / "server-stalls.log"
    log.write_bytes(b"old report\n" * (MAX_LOG_BYTES // 10 + 1))
    log.chmod(0o644)
    run_probe(
        tmp_path,
        """
async def main():
    with watch_stalls(log, timeout=0.5):
        await asyncio.sleep(0)
asyncio.run(main())
""",
    )
    assert log.stat().st_size < MAX_LOG_BYTES
    assert log.stat().st_mode & 0o777 == 0o600
    assert "old report" not in log.read_text()
    previous = log.with_suffix(".previous.log")
    assert previous.read_bytes().startswith(b"old report\n")
    assert previous.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize("kind", ["symlink", "directory", "fifo"])
def test_unusable_log_does_not_prevent_serving(tmp_path: Path, kind: str) -> None:
    log = tmp_path / "server-stalls.log"
    target = tmp_path / "untouched"
    target.write_text("keep")
    if kind == "symlink":
        log.symlink_to(target)
    elif kind == "directory":
        log.mkdir()
    else:
        os.mkfifo(log)
    stderr = run_probe(
        tmp_path,
        """
async def main():
    with watch_stalls(log, timeout=0.5):
        await asyncio.sleep(0)
    print('served')
asyncio.run(main())
""",
    )
    assert "diagnostics unavailable" in stderr
    assert target.read_text() == "keep"
