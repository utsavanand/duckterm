"""Internal helpers must ignore project Python settings in embedded builds."""

import subprocess
import sys
from pathlib import Path

from duckterm.helpers.runtime import module_command


def test_module_command_preserves_interpreter_and_arguments():
    command = module_command("duckterm.cli", "--version")
    assert command[0] == sys.executable
    assert command[-3:] == ["-m", "duckterm.cli", "--version"]
    assert ("-I" in command) == bool(sys.flags.isolated)
    assert ("-B" in command) == sys.dont_write_bytecode


def test_isolated_helpers_ignore_hostile_python_settings(tmp_path):
    from duckterm.helpers import runtime

    code = (
        "import runpy, subprocess; "
        f"command = runpy.run_path({str(Path(runtime.__file__).resolve())!r})['module_command']; "
        "args = command('json.tool', '--help'); "
        "assert '-I' in args and '-B' in args; "
        "subprocess.run(args, check=True, stdout=subprocess.DEVNULL)"
    )
    subprocess.run(
        [sys.executable, "-I", "-B", "-c", code],
        env={"PYTHONHOME": str(tmp_path / "missing"), "PYTHONPATH": str(tmp_path)},
        check=True,
    )
