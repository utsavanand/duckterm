"""Hosted secrets are fetched only by the separate connector service identity."""

import base64
import json
import re
import urllib.error
import urllib.request
from typing import Protocol


class SecretStore(Protocol):
    def read(self, reference: str) -> dict[str, str]: ...


class GcpSecretStore:
    """Use the broker VM's attached identity; never a downloaded account key.

    References pin an enabled numeric version. Rotation is an explicit policy
    change so existing processes cannot silently switch credentials.
    """

    def read(self, reference: str) -> dict[str, str]:
        if not re.fullmatch(
            r"projects/[a-z0-9-]+/secrets/[A-Za-z0-9_-]+/versions/[1-9][0-9]*", reference
        ):
            raise ValueError("secret reference must pin a numeric Secret Manager version")
        token_request = urllib.request.Request(
            "http://169.254.169.254/computeMetadata/v1/instance/service-accounts/default/token",
            headers={"Metadata-Flavor": "Google"},
        )
        try:
            # Ignore ambient proxy variables for the link-local identity request.
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with opener.open(token_request, timeout=5) as response:
                access_token = json.load(response)["access_token"]
            request = urllib.request.Request(
                f"https://secretmanager.googleapis.com/v1/{reference}:access",
                headers={"Authorization": f"Bearer {access_token}"},
            )
            with urllib.request.urlopen(request, timeout=10) as response:
                payload = json.load(response)["payload"]
            decoded = base64.b64decode(payload["data"], validate=True)
            checksum = 0xFFFFFFFF
            for byte in decoded:
                checksum ^= byte
                for _ in range(8):
                    checksum = (checksum >> 1) ^ (0x82F63B78 if checksum & 1 else 0)
            if (checksum ^ 0xFFFFFFFF) != int(payload["dataCrc32c"]):
                raise ValueError("Secret payload checksum mismatch")
            value = json.loads(decoded)
        except (OSError, KeyError, ValueError) as exc:
            # Never put provider response bodies, headers or payloads in errors.
            raise RuntimeError(
                "Connector service could not access its configured secret version"
            ) from exc
        if not isinstance(value, dict) or not all(
            isinstance(k, str) and isinstance(v, str) for k, v in value.items()
        ):
            raise RuntimeError("Connector credential must be a JSON object of strings")
        return value
