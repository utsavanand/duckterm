"""Reject a Mac bundle that depends on newer macOS or build-machine libraries."""

import plistlib
import re
import subprocess
import sys
from pathlib import Path

MAGICS = {bytes.fromhex(value) for value in ("cffaedfe", "feedfacf", "cafebabe", "bebafeca")}


def version(value: str) -> tuple[int, ...]:
    parts = tuple(map(int, value.split(".")))
    return parts + (0,) * (3 - len(parts))


def verify(app: Path) -> int:
    app = app.resolve()
    info = plistlib.loads((app / "Contents/Info.plist").read_bytes())
    minimum = version(info["LSMinimumSystemVersion"])
    count = 0
    for path in app.rglob("*"):
        if path.is_symlink():
            if not path.resolve().is_relative_to(app) or not path.exists():
                raise ValueError(f"Broken or external bundle symlink: {path}")
            continue
        if not path.is_file():
            continue
        with path.open("rb") as stream:
            if stream.read(4) not in MAGICS:
                continue
        count += 1
        arches = subprocess.check_output(["lipo", "-archs", str(path)], text=True).split()
        if "arm64" not in arches:
            raise ValueError(f"Missing arm64 code: {path}")
        loads = subprocess.check_output(["otool", "-arch", "arm64", "-l", str(path)], text=True)
        commands = re.split(r"Load command \d+\n", loads)[1:]
        target = re.search(r"\bminos ([\d.]+)", loads)
        if target is None:
            target = re.search(r"LC_VERSION_MIN_MACOSX\s+cmdsize \d+\s+version ([\d.]+)", loads)
        if target is None or version(target[1]) > minimum:
            raise ValueError(f"Unsupported macOS minimum: {path}")
        rpaths = [
            match[1]
            for command in commands
            if "cmd LC_RPATH\n" in command
            and (match := re.search(r"\bpath (.*?) \(offset", command))
        ]
        dependencies = [
            match[1]
            for command in commands
            if re.search(r"cmd LC_(?:LOAD|LOAD_WEAK|REEXPORT)_DYLIB\n", command)
            and (match := re.search(r"\bname (.*?) \(offset", command))
        ]
        for dependency in dependencies:
            candidates = (
                [entry + dependency[len("@rpath") :] for entry in rpaths]
                if dependency.startswith("@rpath/")
                else [dependency]
            )
            resolved = False
            for candidate in candidates:
                if candidate.startswith(("/usr/lib/", "/System/Library/")):
                    resolved = True  # System libraries may live only in the dyld cache.
                    break
                for executable_dir in (
                    path.parent,
                    app / "Contents/MacOS",
                    app / "Contents/Resources/python/bin",
                ):
                    expanded = candidate.replace("@loader_path", str(path.parent)).replace(
                        "@executable_path", str(executable_dir)
                    )
                    target_path = Path(expanded)
                    if (
                        target_path.is_absolute()
                        and target_path.resolve().is_relative_to(app)
                        and target_path.is_file()
                    ):
                        resolved = True
                        break
                if resolved:
                    break
            if not resolved:
                raise ValueError(f"Unbundled dependency in {path}: {dependency}")
    if count < 4:
        raise ValueError("Native runtime components are missing")
    return count


if __name__ == "__main__":
    print(
        f"Verified {verify(Path(sys.argv[1]))} native files: arm64, deployment target and libraries"
    )
