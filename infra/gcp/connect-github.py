"""Interactive local setup: send a hidden token directly to Secret Manager."""

import getpass
import json
import re
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

PROJECT = "duckterm-20260920"


def main() -> None:
    if not sys.stdin.isatty():
        raise SystemExit("Run this setup in an interactive local terminal")
    gcloud = shutil.which("gcloud")
    if not gcloud:
        raise SystemExit("Google Cloud CLI is required")
    print("Connect GitHub to the shared Duckterm remote workspace.")
    print("1: Reuse this Mac's GitHub CLI authorization (shared permissions and revocation)")
    print("2: Enter a separate GitHub token")
    source = input("Credential source [1/2]: ").strip()
    if source == "1":
        credential = subprocess.run(
            ["gh", "auth", "token"], capture_output=True, text=True, check=True
        )
        token = credential.stdout.strip()
        del credential
    elif source == "2":
        token = getpass.getpass("GitHub token (hidden): ").strip()
    else:
        raise SystemExit("Cancelled; no credential stored")
    if not token:
        raise SystemExit("Cancelled; no credential stored")
    request = urllib.request.Request(
        "https://api.github.com/user",
        headers={"Authorization": f"Bearer {token}", "User-Agent": "duckterm-setup"},
    )
    with urllib.request.urlopen(request, timeout=15) as response:
        identity = json.load(response)["login"]
    del request
    if input(f"Enable remote GitHub access as {identity}? [y/N]: ").strip().lower() != "y":
        raise SystemExit("Cancelled; no credential stored")
    result = subprocess.run(
        [
            gcloud,
            "secrets",
            "versions",
            "add",
            "duckterm-github",
            f"--project={PROJECT}",
            "--data-file=-",
            "--format=value(name)",
            "--quiet",
        ],
        input=json.dumps({"token": token}),
        text=True,
        capture_output=True,
    )
    del token
    if result.returncode:
        raise SystemExit("Secret upload failed. No connector was enabled.")
    reference = result.stdout.strip()
    match = re.fullmatch(r"projects/[\w-]+/secrets/duckterm-github/versions/(\d+)", reference)
    if not match:
        raise SystemExit(
            "Credential uploaded, but the version response was unexpected. Setup stopped."
        )
    version = match[1]
    print(f"Stored GitHub secret version {version}. Verifying identity on the connector service…")
    result = subprocess.run(
        [
            gcloud,
            "compute",
            "ssh",
            "duckterm-connectors-dev-1",
            f"--project={PROJECT}",
            "--zone=us-central1-a",
            "--tunnel-through-iap",
            "--ssh-key-file=" + str(Path.home() / ".ssh/duckterm_gcp"),
            "--strict-host-key-checking=yes",
            "--quiet",
            "--command=sudo /opt/duckterm/venv/bin/duckterm connector-admin github --version="
            + version,
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode:
        raise SystemExit(
            f"Version {version} was stored, but activation failed. Tell Duckterm setup to check it."
        )
    print("GitHub connected. Return to Duckterm and say setup is complete.")


if __name__ == "__main__":
    main()
