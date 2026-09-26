"""Spooling remains bounded and forwards short interactive output promptly."""

import os
import subprocess
import sys
import time
from pathlib import Path


def test_spool_flushes_short_output_and_rotates(tmp_path: Path) -> None:
    path = tmp_path / "pane.log"
    code = (
        "from duckterm.helpers.pane_log import record; import sys; from pathlib import Path; "
        "record(sys.stdin.buffer,Path(sys.argv[1]),8192)"
    )
    proc = subprocess.Popen([sys.executable, "-c", code, str(path)], stdin=subprocess.PIPE)
    try:
        assert proc.stdin is not None
        proc.stdin.write(b"prompt> ")
        proc.stdin.flush()
        deadline = time.monotonic() + 3
        while (not path.exists() or path.stat().st_size < 8) and time.monotonic() < deadline:
            time.sleep(0.01)
        assert path.read_bytes() == b"prompt> "
        proc.stdin.write(b"x" * 20000)
        proc.stdin.close()
        assert proc.wait(timeout=5) == 0
        assert 0 < path.stat().st_size <= 8192
        assert path.with_suffix(".previous").stat().st_size <= 8192
        assert os.stat(path).st_mode & 0o777 == 0o600
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
