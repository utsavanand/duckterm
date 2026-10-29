"""Keep internal Python helpers on the backend's interpreter and isolation mode."""

import sys


def module_command(module: str, *args: str) -> list[str]:
    flags = ["-I"] if sys.flags.isolated else []
    if sys.dont_write_bytecode:
        flags.append("-B")
    return [sys.executable, *flags, "-m", module, *args]
