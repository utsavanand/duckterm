"""Verify a bundled client uses existing system panes, without touching real sockets."""

import os
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

bundle = str(Path(sys.argv[1]).resolve())
system = sys.argv[2]
socket = "duckterm-test-transition-" + uuid.uuid4().hex
with tempfile.TemporaryDirectory() as home:
    env = dict(os.environ, HOME=home, DUCKTERM_TMUX_SOCKET=socket, DUCKTERM_BUNDLED_TMUX=bundle)

    def run(binary, *args):
        return subprocess.check_output([binary, "-L", socket, *args], env=env, text=True).strip()

    try:
        # One disposable fixture on an explicit private socket, always removed.
        run(system, "-f", "/dev/null", "new-session", "-d", "-s", "rd_test_transition", "/bin/cat")
        before = run(
            system, "display-message", "-p", "-t", "rd_test_transition", "#{pid}:#{pane_pid}"
        )
        code = (
            "from duckterm.agents import tmux; "
            "print(tmux.selected_client()); "
            'ok,out=tmux._tmux("display-message","-p","-t",'
            '"rd_test_transition","#{pid}:#{pane_pid}"); '
            "assert ok; print(out.strip())"
        )
        output = subprocess.check_output([sys.executable, "-c", code], env=env, text=True)
        assert output.splitlines()[-1] == before, (before, output)
        assert (
            run(system, "display-message", "-p", "-t", "rd_test_transition", "#{pid}:#{pane_pid}")
            == before
        )
        print("PASS existing system server and pane PIDs unchanged:", output.strip())
    finally:
        subprocess.run([system, "-L", socket, "kill-server"], env=env, check=False)
