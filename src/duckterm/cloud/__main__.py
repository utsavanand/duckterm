"""Run the account service: python -m duckterm.cloud.

Configuration comes from the environment so the VM's unit file holds it:
DUCKY_CLOUD_DB, DUCKY_CLOUD_URL (public https origin), GITHUB_CLIENT_ID,
GITHUB_CLIENT_SECRET, and optionally DUCKY_CLOUD_PORT, DUCKY_CLOUD_CERT and
DUCKY_CLOUD_KEY for TLS.
"""

import asyncio
import os
import ssl
from pathlib import Path

from duckterm.cloud.github import GitHub
from duckterm.cloud.service import AccountService, serve
from duckterm.cloud.store import Store


async def main() -> None:
    store = Store(Path(os.environ["DUCKY_CLOUD_DB"]))
    github = GitHub(os.environ["GITHUB_CLIENT_ID"], os.environ["GITHUB_CLIENT_SECRET"])
    service = AccountService(store, github, os.environ["DUCKY_CLOUD_URL"])
    context = None
    if cert := os.environ.get("DUCKY_CLOUD_CERT"):
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.load_cert_chain(cert, os.environ["DUCKY_CLOUD_KEY"])
    port = int(os.environ.get("DUCKY_CLOUD_PORT", "8443"))
    listener = await serve(service, "0.0.0.0", port, context)  # noqa: S104 - public service
    async with listener:
        await listener.serve_forever()


if __name__ == "__main__":
    asyncio.run(main())
