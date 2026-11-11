"""Stage an already-built backend wheel beside the Windows executables."""

import argparse
import hashlib
import json
import re
import shutil
import zipfile
from pathlib import Path


def stage(wheel, output):
    if not re.fullmatch(r"duckterm-[0-9][A-Za-z0-9.+]*-py3-none-any\.whl", wheel.name):
        raise ValueError("Expected the built DuckTerm wheel")
    if wheel.stat().st_size > 32 * 1024 * 1024:
        raise ValueError("Backend wheel exceeds 32 MiB")
    with zipfile.ZipFile(wheel) as archive:
        if "duckterm/dashboard/index.html" not in archive.namelist():
            raise ValueError("Build the dashboard into the wheel before packaging Windows")
    payload = output / "payload"
    payload.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(wheel.read_bytes()).hexdigest()
    shutil.copyfile(wheel, payload / wheel.name)
    (payload / "manifest.json").write_text(
        json.dumps({"protocol": 1, "wheel_name": wheel.name, "sha256": digest}) + "\n",
        encoding="utf-8",
    )
    shutil.copyfile(Path(__file__).with_name("README.md"), output / "README.md")
    shutil.copyfile(Path(__file__).parents[2] / "LICENSE", output / "LICENSE-duckterm")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--wheel", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    options = parser.parse_args()
    stage(options.wheel, options.output)
