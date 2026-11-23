"""The release verifier must reject malformed public inputs without crashing."""

import base64
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.skipif(sys.platform != "darwin", reason="CryptoKit requires macOS")


@pytest.fixture(scope="module")
def verifier(tmp_path_factory):
    executable = tmp_path_factory.mktemp("update-signature") / "verify"
    subprocess.run(
        ["swiftc", str(ROOT / "mac/verify-update-signature.swift"), "-o", str(executable)],
        check=True,
        capture_output=True,
    )
    return executable


@pytest.mark.parametrize(
    "key", ["", "not-base64!", *[base64.b64encode(bytes(n)).decode() for n in (1, 31, 33)]]
)
def test_invalid_public_key_returns_usage_error(verifier, tmp_path, key):
    archive = tmp_path / "archive.zip"
    archive.write_bytes(b"test archive")
    result = subprocess.run(
        [str(verifier), key, base64.b64encode(bytes(64)).decode(), str(archive)],
        capture_output=True,
    )
    assert result.returncode == 2, result.stderr


def test_unreadable_archive_returns_usage_error(verifier, tmp_path):
    result = subprocess.run(
        [
            str(verifier),
            base64.b64encode(bytes(32)).decode(),
            base64.b64encode(bytes(64)).decode(),
            str(tmp_path / "missing.zip"),
        ],
        capture_output=True,
    )
    assert result.returncode == 2, result.stderr
