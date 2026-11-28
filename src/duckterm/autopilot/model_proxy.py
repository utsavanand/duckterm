"""Model proxy: sandboxes reach the model API only with a per-run token.

The real API key lives here and never enters a sandbox. Each run gets a token
with a budget and an expiry; the proxy swaps the token for the real key,
streams the reply back unchanged, and charges the reply's token usage to the
run.

Every refusal is HTTP 403 `permission_error`. Claude Code retries 401 and 429
with backoff, so a run refused with either hangs until its step timeout; on
403 it makes one attempt and exits 1 (autopilot auth probe, 2026-11-19).
"""

import argparse
import hmac
import http.client
import json
import os
import secrets
import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

ANTHROPIC = "https://api.anthropic.com"
# Sent with a Claude subscription token (`claude setup-token`) in place of an
# API key; the API refuses OAuth bearer tokens without it.
OAUTH_BETA = "oauth-2025-04-20"
OPENAI = "https://api.openai.com"
# Codex signed in with ChatGPT: its subscription answers the same Responses
# calls at this path, with the account named in a header.
CHATGPT = "https://chatgpt.com"
CHATGPT_RESPONSES = "/backend-api/codex/responses"
UPSTREAM_TIMEOUT = 600
# Cache reads cost a tenth of uncached input at both providers. Charging them
# in full would spend a budget on Claude Code re-reading its own context.
CACHE_READ_WEIGHT = 0.1

# Hop-by-hop headers, plus anything that carries a credential or names the
# proxy. accept-encoding is dropped so replies arrive uncompressed: the proxy
# reads token usage out of them (the real API gzips by default).
_DROP_REQUEST = frozenset(
    {
        "accept-encoding",
        "authorization",
        "x-api-key",
        "host",
        "content-length",
        "connection",
        "keep-alive",
        "proxy-authorization",
        "proxy-connection",
        "te",
        "trailer",
        "transfer-encoding",
        "upgrade",
        "x-duckterm-step",
        "chatgpt-account-id",
    }
)
_DROP_RESPONSE = frozenset(
    {
        "content-length",
        "connection",
        "keep-alive",
        "transfer-encoding",
        "trailer",
        "upgrade",
    }
)


# The only calls a run may make, by method and path, with the query strings the
# harnesses send. Everything else (account, key, batch and file endpoints) is
# refused before it reaches the upstream with the owner's credential: those
# calls carry no usage, so they would bypass the budget, and what they return
# could be written into a branch or pull request.
ALLOWED: dict[tuple[str, str], str] = {
    ("POST", "/v1/messages"): "anthropic",
    ("POST", "/v1/messages/count_tokens"): "anthropic",
    ("POST", "/v1/responses"): "openai",
}
ALLOWED_QUERIES = frozenset({"", "beta=true"})
# Reserved per call until its real usage is known, so parallel calls cannot
# all pass admit() before any of them is charged.
DEFAULT_RESERVE_OUTPUT = 8192


def allowed(method: str, path: str) -> str | None:
    """The provider for an allowed call, else None."""
    route, _, query = path.partition("?")
    return ALLOWED.get((method, route)) if query in ALLOWED_QUERIES else None


def estimate(body: bytes) -> int:
    """A charge to reserve before the reply: the request's size in tokens
    (about four bytes each) plus the output it asks for."""
    try:
        req = json.loads(body or b"{}")
    except ValueError:
        req = {}
    asked = req.get("max_tokens") or req.get("max_output_tokens") if isinstance(req, dict) else None
    output = asked if isinstance(asked, int) and asked > 0 else DEFAULT_RESERVE_OUTPUT
    return len(body) // 4 + output


@dataclass
class Run:
    run_id: str
    budget: int
    expires: float
    used: float = 0
    reserved: int = 0
    refusal: str | None = None


@dataclass
class Usage:
    input: int = 0
    output: int = 0
    cache_read: int = 0
    cache_write: int = 0

    def charge(self) -> float:
        return self.input + self.cache_write + self.output + self.cache_read * CACHE_READ_WEIGHT

    def add_anthropic(self, usage: dict[str, Any]) -> None:
        # message_start carries input counts; message_delta carries the running
        # output total, so later values replace earlier ones.
        for field, key in (
            ("input", "input_tokens"),
            ("output", "output_tokens"),
            ("cache_read", "cache_read_input_tokens"),
            ("cache_write", "cache_creation_input_tokens"),
        ):
            if isinstance(usage.get(key), int) and usage[key]:
                setattr(self, field, usage[key])

    def add_openai(self, usage: dict[str, Any]) -> None:
        cached = (usage.get("input_tokens_details") or {}).get("cached_tokens") or 0
        self.input = (usage.get("input_tokens") or 0) - cached
        self.cache_read = cached
        self.output = usage.get("output_tokens") or 0


class UsageReader:
    """Reads token usage out of a reply as it streams past, SSE or plain JSON."""

    def __init__(self, openai: bool) -> None:
        self.openai = openai
        self.usage = Usage()
        self.model: str | None = None
        self._pending = b""
        self.settled = False
        self.reserved = 0
        self._json: bytearray | None = None
        self._started = False

    def feed(self, chunk: bytes) -> None:
        # A plain JSON reply is kept whole and read at the end; an SSE stream
        # is read line by line and never held.
        if not self._started:
            self._started = True
            if chunk.lstrip()[:1] == b"{":
                self._json = bytearray()
        if self._json is not None:
            self._json += chunk
            return
        self._pending += chunk
        *lines, self._pending = self._pending.split(b"\n")
        for line in lines:
            if line.startswith(b"data:"):
                self._event(line[5:].strip())

    def finish(self) -> Usage:
        if self._json is not None:
            self._event(bytes(self._json))
        elif self._pending.startswith(b"data:"):
            self._event(self._pending[5:].strip())
        return self.usage

    def _event(self, data: bytes) -> None:
        try:
            event = json.loads(data)
        except ValueError:
            return
        if not isinstance(event, dict):
            return
        if self.openai:
            response = event.get("response") if "response" in event else event
            if isinstance(response, dict):
                self.model = response.get("model") or self.model
                if isinstance(response.get("usage"), dict):
                    self.usage.add_openai(response["usage"])
            return
        message = event["message"] if isinstance(event.get("message"), dict) else event
        self.model = message.get("model") or self.model
        for usage in (message.get("usage"), event.get("usage")):
            if isinstance(usage, dict):
                self.usage.add_anthropic(usage)


class ModelProxy:
    def __init__(
        self,
        *,
        anthropic_key: str | None,
        openai_key: str | None,
        log_path: Path,
        anthropic_url: str = ANTHROPIC,
        openai_url: str = OPENAI,
        anthropic_oauth_token: str | None = None,
        chatgpt: tuple[str, str] | None = None,
        chatgpt_url: str = CHATGPT,
    ) -> None:
        """`anthropic_oauth_token` is a Claude subscription token from `claude
        setup-token`, used when there is no API key. `chatgpt` is Codex's
        ChatGPT sign-in (access token, account id), used for Codex calls when
        there is no OpenAI key. Like a key, both stay in the proxy: the sandbox
        only ever holds its run token."""
        self._keys = {"anthropic": anthropic_key, "openai": openai_key}
        self._oauth = anthropic_oauth_token
        self._chatgpt = chatgpt
        self._upstreams = {
            "anthropic": urlsplit(anthropic_url),
            "openai": urlsplit(openai_url),
            "chatgpt": urlsplit(chatgpt_url),
        }
        self._log_path = log_path
        self._runs: dict[str, Run] = {}
        self._lock = threading.Lock()

    def issue(self, run_id: str, *, budget: int, ttl: float) -> str:
        token = "dtrun-" + secrets.token_urlsafe(32)
        with self._lock:
            self._runs[token] = Run(run_id, budget, time.time() + ttl)
        return token

    def revoke(self, token: str) -> None:
        with self._lock:
            self._runs.pop(token, None)

    def run(self, token: str) -> Run | None:
        """The run behind a token: spend so far, and why its last call was
        refused. The runner reads `refusal` to tell budget_exhausted from
        token_expired, since the harness reports both as one 403."""
        with self._lock:
            return self._runs.get(token)

    def admit(self, token: str, reserve: int = 0) -> tuple[Run | None, str | None]:
        """Let a call through, holding `reserve` against the budget until
        charge() replaces it with the real spend."""
        with self._lock:
            run = self._runs.get(token)
            if run is None:
                return None, "unknown_token"
            if time.time() > run.expires:
                run.refusal = "token_expired"
            elif run.used + run.reserved >= run.budget:
                run.refusal = "budget_exhausted"
            else:
                run.reserved += reserve
                return run, None
            return run, run.refusal

    def charge(self, run: Run, usage: Usage, reserved: int = 0) -> None:
        with self._lock:
            run.reserved -= reserved
            run.used += usage.charge()

    def upstream(
        self, provider: str, path: str
    ) -> tuple[http.client.HTTPConnection, dict[str, str], str]:
        """A connection to the provider, the credential headers for it and the
        path to call there."""
        key = self._keys[provider]
        upstream = provider
        if provider == "openai" and key:
            auth = {"authorization": f"Bearer {key}"}
        elif provider == "openai" and self._chatgpt:
            token, account = self._chatgpt
            auth = {"authorization": f"Bearer {token}", "chatgpt-account-id": account}
            upstream, path = "chatgpt", CHATGPT_RESPONSES
        elif provider == "anthropic" and key:
            auth = {"x-api-key": key}
        elif provider == "anthropic" and self._oauth:
            auth = {"authorization": f"Bearer {self._oauth}", "anthropic-beta": OAUTH_BETA}
        elif provider == "openai":
            raise LookupError("no OpenAI key and no Codex ChatGPT sign-in on the run host")
        else:
            raise LookupError(f"no {provider} credential configured")
        url = self._upstreams[upstream]
        cls = http.client.HTTPSConnection if url.scheme == "https" else http.client.HTTPConnection
        return cls(url.netloc, timeout=UPSTREAM_TIMEOUT), auth, path

    def log(self, **fields: Any) -> None:
        line = json.dumps({"ts": round(time.time(), 3), **fields})
        with self._lock, self._log_path.open("a") as f:
            f.write(line + "\n")

    def server(self, host: str, port: int) -> ThreadingHTTPServer:
        proxy = self

        class Handler(_Handler):
            pass

        Handler.proxy = proxy
        server = ThreadingHTTPServer((host, port), Handler)
        server.daemon_threads = True
        return server

    def control_server(self, host: str, port: int, secret: str) -> ThreadingHTTPServer:
        """Where the run host mints and revokes run tokens. Sandboxes share a
        network with the proxy, so this is guarded by a secret they never see."""
        proxy = self

        class Handler(_ControlHandler):
            pass

        Handler.proxy = proxy
        Handler.secret = secret
        server = ThreadingHTTPServer((host, port), Handler)
        server.daemon_threads = True
        return server


class _Handler(BaseHTTPRequestHandler):
    proxy: ModelProxy
    protocol_version = "HTTP/1.1"

    def _token(self) -> str:
        auth = self.headers.get("authorization", "")
        if auth.lower().startswith("bearer "):
            return auth[7:].strip()
        return self.headers.get("x-api-key", "").strip()

    def _refuse(self, why: str, run: Run | None) -> None:
        self.proxy.log(
            run=run.run_id if run else None,
            step=self.headers.get("x-duckterm-step"),
            path=self.path,
            allowed=False,
            why=why,
        )
        body = json.dumps(
            {"type": "error", "error": {"type": "permission_error", "message": why}}
        ).encode()
        self.send_response(403)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _handle(self) -> None:
        length = int(self.headers.get("content-length") or 0)
        body = self.rfile.read(length) if length else b""
        provider = allowed(self.command, self.path)
        if provider is None:
            run = self.proxy.run(self._token())
            return self._refuse("path_not_allowed", run)
        reserve = estimate(body)
        run, refusal = self.proxy.admit(self._token(), reserve)
        if refusal or run is None:
            return self._refuse(refusal or "unknown_token", run)
        try:
            conn, auth, path = self.proxy.upstream(provider, self.path)
        except LookupError as exc:
            self.proxy.charge(run, Usage(), reserve)
            return self._refuse(str(exc), run)
        headers = {k.lower(): v for k, v in self.headers.items() if k.lower() not in _DROP_REQUEST}
        if "anthropic-beta" in auth and headers.get("anthropic-beta"):
            auth["anthropic-beta"] = headers["anthropic-beta"] + "," + auth["anthropic-beta"]
        headers.update(auth)
        reader = UsageReader(openai=provider == "openai")
        reader.reserved = reserve
        status = None
        try:
            try:
                conn.request(self.command, path, body=body, headers=headers)
                resp = conn.getresponse()
            except OSError:
                # 502 is retried by both harnesses, which suits a network blip.
                status = 502
                self.send_response(502)
                self.send_header("content-length", "0")
                self.end_headers()
                return
            status = resp.status
            self.send_response(resp.status)
            for k, v in resp.getheaders():
                if k.lower() not in _DROP_RESPONSE:
                    self.send_header(k, v)
            self.send_header("transfer-encoding", "chunked")
            self.end_headers()
            for chunk in _chunks(resp):
                reader.feed(chunk)
                self.wfile.write(b"%x\r\n%s\r\n" % (len(chunk), chunk))
                self.wfile.flush()
            # Charge before the closing chunk: once the harness has the whole
            # reply it may send the next call, which must see this spend.
            self._settle(run, reader, status)
            self.wfile.write(b"0\r\n\r\n")
        finally:
            conn.close()
            self._settle(run, reader, status)

    def _settle(self, run: Run, reader: UsageReader, status: int | None) -> None:
        """Charge and log a call once, whether it ended normally or not."""
        if reader.settled:
            return
        reader.settled = True
        usage = reader.finish()
        self.proxy.charge(run, usage, reader.reserved)
        self.proxy.log(
            run=run.run_id,
            step=self.headers.get("x-duckterm-step"),
            path=self.path,
            model=reader.model,
            status=status,
            allowed=True,
            input=usage.input,
            output=usage.output,
            cache_read=usage.cache_read,
            cache_write=usage.cache_write,
        )

    do_GET = do_POST = do_PUT = do_DELETE = do_PATCH = _handle

    def log_message(self, *args: Any) -> None:
        pass


class _ControlHandler(BaseHTTPRequestHandler):
    proxy: ModelProxy
    secret: str

    def _reply(self, status: int, value: dict[str, Any] | None = None) -> None:
        body = json.dumps(value).encode() if value is not None else b""
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _authorized(self) -> bool:
        given = self.headers.get("authorization", "").removeprefix("Bearer ")
        if hmac.compare_digest(given.encode(), self.secret.encode()):
            return True
        self._reply(401, {"error": "control secret required"})
        return False

    def do_POST(self) -> None:
        if not self._authorized():
            return
        if self.path != "/runs":
            return self._reply(404, {"error": "not found"})
        try:
            req = json.loads(self.rfile.read(int(self.headers.get("content-length") or 0)))
            token = self.proxy.issue(
                str(req["run_id"]), budget=int(req["budget"]), ttl=float(req["ttl"])
            )
        except (ValueError, KeyError, TypeError):
            return self._reply(400, {"error": "need run_id, budget and ttl"})
        self._reply(201, {"token": token})

    def do_GET(self) -> None:
        if not self._authorized():
            return
        run = self.proxy.run(self.path.removeprefix("/runs/"))
        if run is None:
            return self._reply(404, {"error": "no such run token"})
        self._reply(
            200,
            {
                "run_id": run.run_id,
                "budget": run.budget,
                "used": run.used,
                "expires": run.expires,
                "refusal": run.refusal,
            },
        )

    def do_DELETE(self) -> None:
        if not self._authorized():
            return
        self.proxy.revoke(self.path.removeprefix("/runs/"))
        self._reply(204)

    def log_message(self, *args: Any) -> None:
        pass


def main() -> None:
    parser = argparse.ArgumentParser(description="Autopilot model proxy")
    parser.add_argument("--listen", default="0.0.0.0:8080")
    parser.add_argument("--control", default="0.0.0.0:8081")
    parser.add_argument("--log", default="/var/log/duckterm/model-calls.jsonl")
    args = parser.parse_args()
    # Credentials come from a read-only mounted file, not the container's
    # environment, so they stay out of `docker inspect` and process lists.
    creds_path = os.environ.get("DUCKTERM_PROXY_SECRETS")
    creds = json.loads(Path(creds_path).read_text()) if creds_path else dict(os.environ)
    proxy = ModelProxy(
        anthropic_key=creds.get("ANTHROPIC_API_KEY"),
        openai_key=creds.get("OPENAI_API_KEY"),
        log_path=Path(args.log),
        anthropic_url=os.environ.get("DUCKTERM_ANTHROPIC_URL", ANTHROPIC),
        openai_url=os.environ.get("DUCKTERM_OPENAI_URL", OPENAI),
        anthropic_oauth_token=creds.get("CLAUDE_CODE_OAUTH_TOKEN"),
        chatgpt=(creds["CHATGPT_ACCESS_TOKEN"], creds["CHATGPT_ACCOUNT_ID"])
        if creds.get("CHATGPT_ACCESS_TOKEN") and creds.get("CHATGPT_ACCOUNT_ID")
        else None,
        chatgpt_url=os.environ.get("DUCKTERM_CHATGPT_URL", CHATGPT),
    )
    host, port = args.control.rsplit(":", 1)
    control = proxy.control_server(host, int(port), creds["DUCKTERM_PROXY_CONTROL_SECRET"])
    threading.Thread(target=control.serve_forever, daemon=True).start()
    host, port = args.listen.rsplit(":", 1)
    proxy.server(host, int(port)).serve_forever()


def _chunks(resp: http.client.HTTPResponse) -> Iterator[bytes]:
    while chunk := resp.read1(65536):
        yield chunk


if __name__ == "__main__":
    main()
