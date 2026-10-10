"""Pinned OpenCode wire formats; no internal database parsing or implicit retries.

These synchronous reads run off the owning server loop. Native write callers
must retain their operation IDs and reconcile uncertain responses themselves.
"""

from __future__ import annotations

import base64
import http.client
import json
import math
import re
import time
from dataclasses import dataclass, field
from typing import Any

VERSION = "1.18.34"
MAX_RESPONSE = 2 * 1024 * 1024
READ_TIMEOUT = 5.0
_NATIVE_ID = re.compile(r"[A-Za-z0-9_-]{1,200}\Z")


class OpenCodeError(ValueError):
    pass


class UncertainWrite(OpenCodeError):
    """The request may have reached native OpenCode; never blindly resend it."""


def model_parts(value: str) -> tuple[str, str]:
    if (
        not isinstance(value, str)
        or len(value.encode()) > 512
        or any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in value)
        or "/" not in value
    ):
        raise OpenCodeError("Use an OpenCode model ID in provider/model format")
    provider, model = value.split("/", 1)
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", provider) or not model or provider.startswith("-"):
        raise OpenCodeError("Use an OpenCode model ID in provider/model format")
    return provider, model


def native_id(value: object) -> str:
    if not isinstance(value, str) or not _NATIVE_ID.fullmatch(value):
        raise OpenCodeError("Invalid native OpenCode identity")
    return value


@dataclass(frozen=True)
class NativeClient:
    port: int
    password: str = field(repr=False)
    directory: str
    timeout: float = READ_TIMEOUT
    limit: int = MAX_RESPONSE

    def __post_init__(self) -> None:
        if type(self.port) is not int or not 0 < self.port < 65536:
            raise OpenCodeError("Invalid native loopback port")
        if not self.password or any(c in self.password for c in "\r\n"):
            raise OpenCodeError("Missing or invalid native endpoint credential")
        if not self.directory.startswith("/") or any(c in self.directory for c in "\r\n"):
            raise OpenCodeError("Native project path must be absolute")
        if not 0 < self.timeout <= READ_TIMEOUT or not 0 < self.limit <= MAX_RESPONSE:
            raise OpenCodeError("Invalid native request bounds")

    def request(self, method: str, path: str, body: dict[str, Any] | None = None) -> Any:
        if method not in {"GET", "POST", "DELETE"}:
            raise OpenCodeError("Unsupported native request method")
        if not path.startswith("/") or path.startswith("//") or any(c in path for c in "\r\n"):
            raise OpenCodeError("Invalid native request path")
        if body is not None and method == "GET":
            raise OpenCodeError("A native read cannot contain a write body")
        payload = json.dumps(body).encode() if body is not None else None
        if payload is not None and len(payload) > self.limit:
            raise OpenCodeError("Native request exceeds the size bound")
        auth = base64.b64encode(("opencode:" + self.password).encode()).decode()
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=self.timeout)
        deadline = time.monotonic() + self.timeout
        attempted = False
        try:
            conn.connect()
            # A direct loopback connection ignores environment proxy settings.
            sock = conn.sock
            assert sock is not None
            attempted = True
            conn.request(
                method,
                path,
                payload,
                {
                    "Authorization": "Basic " + auth,
                    "Content-Type": "application/json",
                    "x-opencode-directory": self.directory,
                },
            )
            sock.settimeout(max(0.001, deadline - time.monotonic()))
            response = conn.getresponse()
            chunks = bytearray()
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("Native response deadline exceeded")
                # HTTPConnection may clear its reference after Connection: close;
                # retaining the original socket keeps the total read deadline.
                sock.settimeout(remaining)
                chunk = response.read1(min(65536, self.limit + 1 - len(chunks)))
                if not chunk:
                    break
                chunks.extend(chunk)
                if len(chunks) > self.limit:
                    raise OpenCodeError("Native response exceeds the size bound")
            if not 200 <= response.status < 300:
                # Never expose provider/server error bodies or follow redirects.
                raise OpenCodeError(f"Native OpenCode returned HTTP {response.status}")
            return json.loads(chunks)
        except (OSError, http.client.HTTPException, ValueError) as exc:
            if method != "GET" and attempted:
                raise UncertainWrite(
                    "Native write result is uncertain; reconcile before retrying"
                ) from exc
            if isinstance(exc, OpenCodeError):
                raise
            raise OpenCodeError("Could not read a valid bounded native response") from exc
        finally:
            conn.close()

    def health(self) -> None:
        result = self.request("GET", "/global/health")
        if (
            not isinstance(result, dict)
            or result.get("healthy") is not True
            or result.get("version") != VERSION
        ):
            raise OpenCodeError("This adapter requires verified OpenCode " + VERSION)

    def messages(self, identity: str) -> list[dict[str, Any]]:
        return messages(
            self.request("GET", "/session/" + native_id(identity) + "/message"), identity
        )


def models(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, dict) or not isinstance(value.get("all"), list):
        raise OpenCodeError("Native OpenCode did not return a provider catalog")
    result = []
    connected = value.get("connected")
    if not isinstance(connected, list):
        connected = []
    for provider in value["all"]:
        if not isinstance(provider, dict) or not isinstance(provider.get("models"), dict):
            raise OpenCodeError("Malformed native provider catalog")
        for key, model in provider["models"].items():
            identity = str(provider.get("id", "")) + "/" + key
            model_parts(identity)
            if not isinstance(model, dict):
                raise OpenCodeError("Malformed native model catalog")
            limit = model.get("limit") or {}
            context = limit.get("context") if isinstance(limit, dict) else None
            result.append(
                {
                    "id": identity,
                    "label": model["name"] if isinstance(model.get("name"), str) else identity,
                    "configured": provider["id"] in connected,
                    "contextWindow": context if type(context) is int and context > 0 else None,
                }
            )
            if len(result) > 10000:
                raise OpenCodeError("Native model catalog exceeds the entry bound")
    return result


def _measurement(value: Any) -> int | float | None:
    if type(value) is int:
        return int(value) if 0 <= value <= 2**53 else None
    if type(value) is float and value >= 0 and math.isfinite(value):
        return float(value)
    return None


def messages(value: Any, identity: str) -> list[dict[str, Any]]:
    native_id(identity)
    if not isinstance(value, list):
        raise OpenCodeError("Native OpenCode did not return message records")
    result = []
    seen = set()
    for record in value:
        if (
            not isinstance(record, dict)
            or not isinstance(record.get("info"), dict)
            or not isinstance(record.get("parts"), list)
        ):
            raise OpenCodeError("Malformed native message record")
        info = record["info"]
        mid = native_id(info.get("id"))
        if (
            info.get("sessionID") != identity
            or mid in seen
            or info.get("role") not in {"user", "assistant"}
        ):
            raise OpenCodeError("Native message identity is ambiguous")
        seen.add(mid)
        blocks = []
        part_ids = set()
        for part in record["parts"]:
            if not isinstance(part, dict):
                raise OpenCodeError("Malformed native message part")
            pid = native_id(part.get("id"))
            if part.get("sessionID") != identity or part.get("messageID") != mid or pid in part_ids:
                raise OpenCodeError("Native message part identity is ambiguous")
            part_ids.add(pid)
            kind = part.get("type")
            if kind in {"text", "reasoning"} and isinstance(part.get("text"), str):
                blocks.append({"id": pid, "type": kind, "text": part["text"]})
            elif kind == "tool" and isinstance(part.get("state"), dict):
                state = part["state"]
                blocks.append(
                    {
                        "id": pid,
                        "type": "tool_use",
                        "name": part.get("tool"),
                        "input": state.get("input"),
                        "status": state.get("status"),
                        "output": state.get("output"),
                        "error": state.get("error"),
                    }
                )
            else:
                blocks.append({"id": pid, "type": "unsupported", "nativeType": kind})
        usage = info.get("tokens") or {}
        if not isinstance(usage, dict):
            usage = {}
        cache = usage.get("cache") or {}
        if not isinstance(cache, dict):
            cache = {}
        result.append(
            {
                "id": mid,
                "role": info["role"],
                "blocks": blocks,
                "provider": info.get("providerID"),
                "model": info.get("modelID"),
                "usage": {
                    **{k: _measurement(usage.get(k)) for k in ("input", "output", "reasoning")},
                    "cacheRead": _measurement(cache.get("read")),
                    "cacheWrite": _measurement(cache.get("write")),
                },
                "cost": _measurement(info.get("cost")),
                "costSource": "provider-reported",
                "complete": isinstance(info.get("time"), dict)
                and bool(info["time"].get("completed")),
            }
        )
    return result
