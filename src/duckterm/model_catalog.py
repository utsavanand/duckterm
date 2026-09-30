"""Read model choices from installed CLIs without starting an agent conversation."""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import re
import tempfile
import time
from typing import Any


class CatalogError(ValueError):
    pass


def normalize(runtime: str, rows: Any) -> list[dict[str, str]]:
    if not isinstance(rows, list):
        raise CatalogError("The CLI did not report a model list.")
    result: list[dict[str, str]] = []
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, dict) or row.get("hidden"):
            continue
        if runtime == "codex":
            model = row.get("model") or row.get("id")
            label = row.get("displayName") or model
        else:
            resolved = row.get("resolvedModel")
            value = row.get("value")
            model = resolved or value
            if isinstance(value, str) and "[" in value and isinstance(resolved, str):
                model = resolved + value[value.index("[") :]
            # Never advertise aliases like Default or Opus as an exact version.
            if not isinstance(model, str) or not re.search(r"\d", model):
                continue
            match = re.fullmatch(r"claude-([a-z]+)-(\d+)(?:-(\d+))?(?:-\d{8})?(\[.*\])?", model)
            label = (
                f"Claude {match[1].title()} {match[2]}"
                + (f".{match[3]}" if match[3] else "")
                + (f" ({match[4][1:-1].upper()})" if match[4] else "")
                if match
                else model
            )
        if not isinstance(model, str) or not model.strip() or len(model) > 200:
            continue
        if any(ord(c) < 32 for c in model) or model in seen:
            continue
        seen.add(model)
        result.append({"id": model, "label": label if isinstance(label, str) else model})
    if not result:
        raise CatalogError("The CLI did not report any available models. Retry after signing in.")
    return result


async def discover(runtime: str) -> list[dict[str, str]]:
    if runtime not in {"codex", "claude-code"}:
        raise CatalogError("This harness does not report model choices.")
    args = (
        ["codex", "app-server"]
        if runtime == "codex"
        else [
            "claude",
            "-p",
            "--input-format",
            "stream-json",
            "--output-format",
            "stream-json",
            "--verbose",
            "--no-session-persistence",
            "--strict-mcp-config",
            "--mcp-config",
            '{"mcpServers":{}}',
            "--settings",
            '{"disableAllHooks":true}',
            "--setting-sources",
            "user",
            "--tools",
            "",
        ]
    )
    env = dict(os.environ)
    if runtime == "claude-code":
        env["CLAUDE_CODE_ENTRYPOINT"] = "sdk-py"
    with tempfile.TemporaryDirectory(prefix="duckterm-models-") as cwd:
        try:
            proc = await asyncio.create_subprocess_exec(
                *args,
                cwd=cwd,
                env=env,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
                limit=1024 * 1024,
            )
        except OSError as exc:
            raise CatalogError("Could not start the installed CLI to read model choices.") from exc
        try:
            assert proc.stdin is not None and proc.stdout is not None

            async def send(value: dict[str, Any]) -> None:
                assert proc.stdin is not None
                proc.stdin.write((json.dumps(value) + "\n").encode())
                await proc.stdin.drain()

            async with asyncio.timeout(20):
                if runtime == "codex":
                    await send(
                        {
                            "id": 1,
                            "method": "initialize",
                            "params": {
                                "clientInfo": {"name": "duckterm-model-catalog", "version": "1"}
                            },
                        }
                    )
                else:
                    await send(
                        {
                            "type": "control_request",
                            "request_id": "catalog",
                            "request": {"subtype": "initialize"},
                        }
                    )
                size = 0
                rows: list[Any] = []
                while line := await proc.stdout.readline():
                    size += len(line)
                    if size > 4 * 1024 * 1024:
                        raise CatalogError("The CLI model response was too large.")
                    data = json.loads(line)
                    if not isinstance(data, dict):
                        continue
                    if runtime == "codex":
                        if data.get("id") == 1:
                            if data.get("error"):
                                raise CatalogError(
                                    "The CLI could not initialize its model catalog."
                                )
                            await send({"method": "initialized", "params": {}})
                            await send(
                                {
                                    "id": 2,
                                    "method": "model/list",
                                    "params": {"includeHidden": False, "limit": 100},
                                }
                            )
                        elif data.get("id") == 2:
                            result = data.get("result")
                            if not isinstance(result, dict) or not isinstance(
                                result.get("data"), list
                            ):
                                raise CatalogError("The CLI could not list its available models.")
                            rows.extend(result["data"])
                            cursor = result.get("nextCursor")
                            if cursor:
                                await send(
                                    {
                                        "id": 2,
                                        "method": "model/list",
                                        "params": {
                                            "includeHidden": False,
                                            "limit": 100,
                                            "cursor": cursor,
                                        },
                                    }
                                )
                            else:
                                return normalize(runtime, rows)
                    elif data.get("type") == "control_response":
                        response = data.get("response", {})
                        if isinstance(response, dict) and response.get("request_id") == "catalog":
                            payload = response.get("response", {})
                            return normalize(
                                runtime,
                                payload.get("models") if isinstance(payload, dict) else None,
                            )
                raise CatalogError("The CLI exited before reporting model choices.")
        except (TimeoutError, json.JSONDecodeError, ConnectionError, ValueError) as exc:
            if isinstance(exc, CatalogError):
                raise
            raise CatalogError(
                "Could not read model choices. Check CLI sign-in and retry."
            ) from exc
        finally:
            if proc.returncode is None:
                with contextlib.suppress(ProcessLookupError):
                    proc.kill()
            await proc.wait()


class ModelCatalog:
    """Lazy, short-lived cache; one subprocess per harness, not per polling viewer."""

    def __init__(self) -> None:
        self.cache: dict[str, tuple[float, list[dict[str, str]]]] = {}
        self.locks: dict[str, asyncio.Lock] = {}

    async def choices(self, runtime: str) -> list[dict[str, str]]:
        async with self.locks.setdefault(runtime, asyncio.Lock()):
            cached = self.cache.get(runtime)
            if cached and time.monotonic() - cached[0] < 60:
                return cached[1]
            rows = await discover(runtime)
            self.cache[runtime] = (time.monotonic(), rows)
            return rows
