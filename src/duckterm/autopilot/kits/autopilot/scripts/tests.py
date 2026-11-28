#!/usr/bin/env python3
"""Flow exec step: run the repository's test command and route on its exit
code, so whether tests passed is never a model's opinion. The command comes
from the autopilot's settings (`test_command`), split like a shell would but
run without one. No command set: pass, and say so.

stdin: {"settings": "<{\"test_command\": str} JSON>"}. Prints one JSON line
with the last part of the output as `log`."""

import json
import shlex
import subprocess
import sys
from typing import Any

LOG_TAIL = 8000


def main() -> None:
    inputs: dict[str, Any] = json.load(sys.stdin)
    try:
        settings = json.loads(inputs.get("settings") or "{}")
    except ValueError:
        settings = {}
    command = str(settings.get("test_command") or "").strip()
    if not command:
        print(json.dumps({"choice": "pass", "reason": "no test command set", "outputs": {}}))
        return
    try:
        done = subprocess.run(shlex.split(command), capture_output=True, text=True, timeout=14 * 60)
        code, log = done.returncode, done.stdout + done.stderr
    except (OSError, ValueError) as exc:
        code, log = 127, f"could not run {command!r}: {exc}"
    except subprocess.TimeoutExpired as exc:
        partial = (
            exc.stdout.decode(errors="replace") if isinstance(exc.stdout, bytes) else exc.stdout
        )
        code, log = 124, f"{command!r} ran past 14 minutes\n{partial or ''}"
    choice = "pass" if code == 0 else "fail"
    reason = f"`{command}` exited {code}"
    print(json.dumps({"choice": choice, "reason": reason, "outputs": {"log": log[-LOG_TAIL:]}}))


if __name__ == "__main__":
    main()
