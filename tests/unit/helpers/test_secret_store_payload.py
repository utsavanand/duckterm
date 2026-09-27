"""The real REST reader verifies data integrity and redacts provider failures."""

import base64
import io
import json
import urllib.request

import pytest

from duckterm.helpers.secret_store import GcpSecretStore


def crc32c(data: bytes) -> int:
    # Independent known-vector helper for the tiny test payload.
    crc = 0xFFFFFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ (0x82F63B78 if crc & 1 else 0)
    return crc ^ 0xFFFFFFFF


def test_rest_reader_checks_checksum_and_does_not_return_cloud_token(monkeypatch) -> None:
    content = b'{"token":"connector-test-value"}'
    requests: list[urllib.request.Request] = []

    class Identity:
        def open(self, request, timeout):
            assert request.full_url.startswith("http://169.254.169.254/")
            return io.BytesIO(b'{"access_token":"cloud-test-token"}')

    def read(request, timeout):
        requests.append(request)
        return io.BytesIO(
            json.dumps(
                {
                    "payload": {
                        "data": base64.b64encode(content).decode(),
                        "dataCrc32c": str(crc32c(content)),
                    }
                }
            ).encode()
        )

    monkeypatch.setattr(urllib.request, "build_opener", lambda *args: Identity())
    monkeypatch.setattr(urllib.request, "urlopen", read)
    result = GcpSecretStore().read("projects/test/secrets/github/versions/1")
    assert result == {"token": "connector-test-value"}
    assert requests[0].get_header("Authorization") == "Bearer cloud-test-token"

    def corrupt(request, timeout):
        return io.BytesIO(b'{"payload":{"data":"e30=","dataCrc32c":"0"}}')

    monkeypatch.setattr(urllib.request, "urlopen", corrupt)
    with pytest.raises(RuntimeError, match="could not access"):
        GcpSecretStore().read("projects/test/secrets/github/versions/1")
