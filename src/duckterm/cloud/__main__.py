"""Run the account service: python -m duckterm.cloud.

Configuration comes from the environment so the VM's unit file holds it:
DUCKY_CLOUD_DB, DUCKY_CLOUD_URL (public https origin), GITHUB_CLIENT_ID,
GITHUB_CLIENT_SECRET, DUCKY_CLOUD_CERT and DUCKY_CLOUD_KEY, and optionally
DUCKY_CLOUD_PORT. DUCKY_CLOUD_PLAINTEXT=1 runs without TLS, for local tests only.
"""

import asyncio
import os
import ssl
from collections.abc import Mapping
from pathlib import Path

from duckterm.cloud.github import GitHub
from duckterm.cloud.service import AccountService, serve
from duckterm.cloud.store import Store


def tls_context(environ: Mapping[str, str]) -> ssl.SSLContext | None:
    if environ.get("DUCKY_CLOUD_PLAINTEXT") == "1":
        return None
    if not environ.get("DUCKY_CLOUD_CERT") or not environ.get("DUCKY_CLOUD_KEY"):
        raise SystemExit(
            "Set DUCKY_CLOUD_CERT and DUCKY_CLOUD_KEY; DUCKY_CLOUD_PLAINTEXT=1 is for local tests"
        )
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(environ["DUCKY_CLOUD_CERT"], environ["DUCKY_CLOUD_KEY"])
    return context


async def main() -> None:
    os.umask(0o077)
    context = tls_context(os.environ)
    store = Store(Path(os.environ["DUCKY_CLOUD_DB"]))
    github = GitHub(os.environ["GITHUB_CLIENT_ID"], os.environ["GITHUB_CLIENT_SECRET"])
    service = AccountService(store, github, os.environ["DUCKY_CLOUD_URL"])
    port = int(os.environ.get("DUCKY_CLOUD_PORT", "8443"))
    listener = await serve(service, "0.0.0.0", port, context)  # noqa: S104 - public service
    async with listener:
        await listener.serve_forever()


if __name__ == "__main__":
    asyncio.run(main())
