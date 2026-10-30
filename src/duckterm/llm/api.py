"""Direct provider calls with the owner's own key: the Anthropic Messages API
and OpenAI-compatible Chat Completions (OpenAI, OpenRouter, Ollama, LM Studio,
Groq, ...). Stdlib urllib only; the key is sent to the provider and nowhere else.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

from duckterm.core.session_api import APIError

TIMEOUT = 180
# effort is only sent where it was tested; older Claude models reject it.
EFFORT_MODELS = {"claude-haiku-5-5", "claude-sonnet-5-5", "claude-opus-5-5", "claude-fable-5-1"}


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """urllib copies x-api-key and Authorization onto a redirect's Location even
    when the host changes, and no provider API needs a redirect."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        fp.close()
        raise APIError(502, "Provider redirected; check the base URL")


_open = urllib.request.build_opener(_NoRedirect).open


def _request(url: str, headers: dict[str, str], body: dict[str, Any] | None = None) -> Any:
    req = urllib.request.Request(
        url,
        data=None if body is None else json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "User-Agent": "duckterm", **headers},
        method="GET" if body is None else "POST",
    )
    secrets = [v.removeprefix("Bearer ") for k, v in headers.items() if k.lower() in _KEY_HEADERS]
    try:
        with _open(req, timeout=TIMEOUT) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as exc:
        # Error bodies name the problem ("invalid x-api-key", "model not found"),
        # but a custom endpoint can say anything, including the key it was sent.
        detail = exc.read(2000).decode("utf-8", "replace")
        try:
            message = json.loads(detail).get("error", {}).get("message") or detail
        except (ValueError, AttributeError):
            message = detail
        said = _redacted(str(message), secrets)[:300]
        raise APIError(502, f"Provider returned {exc.code}. Provider said: {said}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        reason = _redacted(str(exc), secrets)
        raise APIError(503, f"Provider could not be reached: {reason}") from exc
    except ValueError as exc:
        raise APIError(502, "Provider returned a reply that is not JSON") from exc


_KEY_HEADERS = {"x-api-key", "authorization"}


def _redacted(text: str, secrets: list[str]) -> str:
    for secret in secrets:
        if secret:
            text = text.replace(secret, "[key]")
    return text


def _anthropic_headers(key: str) -> dict[str, str]:
    return {"x-api-key": key, "anthropic-version": "2023-06-01"}


def _openai_headers(key: str | None) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"} if key else {}


def _closed(schema: Any) -> Any:
    """Structured outputs require every object to forbid unknown keys; the
    review schemas leave that implicit."""
    if isinstance(schema, list):
        return [_closed(item) for item in schema]
    if not isinstance(schema, dict):
        return schema
    out = {k: _closed(v) for k, v in schema.items()}
    if out.get("type") == "object":
        out.setdefault("additionalProperties", False)
    return out


def anthropic(
    base: str,
    key: str,
    model: str,
    system: str,
    prompt: str,
    schema: dict[str, Any] | None,
    effort: str,
) -> str:
    output: dict[str, Any] = {}
    if model in EFFORT_MODELS:
        output["effort"] = effort
    if schema is not None:
        output["format"] = {"type": "json_schema", "schema": _closed(schema)}
    body: dict[str, Any] = {
        "model": model,
        "max_tokens": 16000,
        "system": system,
        "messages": [{"role": "user", "content": prompt}],
    }
    if output:
        body["output_config"] = output
    reply = _request(f"{base}/messages", _anthropic_headers(key), body)
    if reply.get("stop_reason") == "refusal":
        raise APIError(502, "The model declined this request")
    return "".join(b.get("text", "") for b in reply.get("content", []) if b.get("type") == "text")


def openai_chat(
    base: str,
    key: str | None,
    model: str,
    system: str,
    prompt: str,
    schema: dict[str, Any] | None,
) -> str:
    body: dict[str, Any] = {
        "model": model,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
    }
    if schema is not None:
        # Not strict: strict mode requires every property to be required, which
        # the review schemas don't follow. The schema still steers the reply.
        body["response_format"] = {
            "type": "json_schema",
            "json_schema": {"name": "reply", "schema": schema},
        }
    reply = _request(f"{base}/chat/completions", _openai_headers(key), body)
    try:
        return str(reply["choices"][0]["message"]["content"] or "")
    except (KeyError, IndexError, TypeError) as exc:
        raise APIError(502, "Provider returned no message") from exc


def models(wire: str, base: str, key: str | None) -> list[str]:
    """Model ids the key can use. Listing models proves the key works without
    spending tokens or depending on chat parameters."""
    headers = _anthropic_headers(key or "") if wire == "anthropic" else _openai_headers(key)
    reply = _request(f"{base}/models" + ("?limit=1000" if wire == "anthropic" else ""), headers)
    return sorted(str(m["id"]) for m in reply.get("data", []) if isinstance(m, dict) and "id" in m)
