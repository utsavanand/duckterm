"""Seed a new harness conversation without changing the DuckTerm card."""

from __future__ import annotations

import re
import shlex
import uuid
from typing import TYPE_CHECKING, Any

from duckterm.core import events
from duckterm.harnesses import runtime_for

if TYPE_CHECKING:
    from duckterm.server import Server


async def prepare(server: Server, key: str, row: dict[str, Any]) -> dict[str, Any]:
    """Checkpoint before stop; callers must recheck turn/draft after this await."""
    native_id = server.history.session_id_for(key)
    previous = server.history.restart_control(key)
    checkpoint = await server._create_checkpoint(key, row, "Before harness switch")
    brief = server._resume_brief(key, {**row, "outcome_summary": checkpoint["summary"]})
    brief = brief.replace(
        "whose conversation could not be restored.",
        "using a different harness. This is a NEW conversation, not a resumed conversation.",
    )
    if row.get("notes"):
        brief += "\nOwner's saved notes (verify before relying on them):\n" + str(row["notes"])
    return {
        "runtime": row.get("runtime"),
        "native_id": native_id,
        "command": row.get("command"),
        "model": row.get("model"),
        "configured_model": previous.get("configured_model"),
        "native_binding": previous.get("native_binding"),
        "cwd": str(row.get("worktree_path") or row.get("cwd") or "."),
        "test": bool(row.get("test")),
        "checkpoint_id": checkpoint["id"],
        "seed": brief,
    }


def restore(server: Server, key: str) -> None:
    """Restore stopped-card recovery metadata, never start the previous agent."""
    previous = server.restarts.read(key).get("previous_conversation")
    if not previous:
        return
    prior_binding = previous.get("native_binding") or {}
    failed_binding = server.restarts.read(key).get("native_binding") or {}
    retired = list(prior_binding.get("retired_ids") or [])
    failed_id = failed_binding.get("native_id")
    if failed_id and failed_id not in retired:
        retired.append(failed_id)
    control = dict(server.restarts.read(key))
    control.update(
        native_binding={
            "runtime": previous["runtime"],
            "native_id": previous.get("native_id"),
            "generation": uuid.uuid4().hex,
            "retired_ids": retired,
        },
        configured_model=previous.get("configured_model"),
        native_id_pending=False,
    )
    server.history.set_harness_identity(
        key, previous["runtime"], previous.get("command"), previous.get("model"), control=control
    )
    server._set_lifecycle(key, "stopped")


async def launch(
    server: Server, key: str, target: str, binary: str, model: str, prepared: dict[str, Any]
) -> None:
    previous = {k: v for k, v in prepared.items() if k != "seed"}
    old_binding = prepared.get("native_binding") or {}
    retired = list(old_binding.get("retired_ids") or [])
    if prepared.get("native_id") and prepared["native_id"] not in retired:
        retired.append(prepared["native_id"])
    # This boundary is durable before the new process can emit a hook. Until
    # its native start hook arrives, identity is unknown, never the old ID.
    generation = uuid.uuid4().hex
    control = dict(server.restarts.read(key))
    control.update(
        previous_conversation=previous,
        native_binding={
            "runtime": target,
            "native_id": None,
            "retired_ids": retired,
            "generation": generation,
        },
        native_id_pending=True,
    )
    server.history.set_harness_identity(key, target, None, None, control=control)
    adapter = runtime_for(target, binary)
    argv = [binary, *(adapter.model_arguments(model) if model else [])]
    try:
        await server.orchestrator.launch(
            runtime=runtime_for(target, shlex.join(argv)),
            cwd=prepared["cwd"],
            session_key=key,
            prompt=prepared["seed"],
            env={"DUCKTERM_HARNESS_GENERATION": generation},
            record_intention=False,
            test=prepared["test"],
        )
    except BaseException:
        restore(server, key)
        raise
    server.history.clear_heartbeat(key)
    server.bus.publish(
        {
            "event_type": "HarnessSwitched",
            "session_key": key,
            "from_harness": prepared["runtime"],
            "to_harness": target,
            "previous_native_id": prepared["native_id"],
            "checkpoint_id": prepared["checkpoint_id"],
            "context": "seeded_new_conversation",
            "test": prepared["test"],
        }
    )


def accept_hook(server: Server, raw: dict[str, Any]) -> bool:
    """Protect switched cards from late old hooks and native-ID fallthrough.

    Only process-owned start hooks may bind a new conversation here. Shared
    daemon targets are unavailable until the separate nonce resolver ships.
    """
    key = raw.get("session_key")
    if not isinstance(key, str):
        return True
    control = server.restarts.read(key)
    binding = control.get("native_binding")
    if not binding:
        return True
    if (
        raw.get("runtime") != binding["runtime"]
        or raw.get("hook_host") == "daemon"
        or raw.get("launch_generation") != binding.get("generation")
    ):
        return False
    native_id = raw.get("session_id")
    if binding.get("native_id"):
        return bool(native_id == binding["native_id"])
    if (
        raw.get("event_type") != events.SESSION_START
        or raw.get("agent_id")
        or not isinstance(native_id, str)
        or not re.fullmatch(r"[A-Za-z0-9_-]{1,200}", native_id)
        or native_id in binding.get("retired_ids", [])
    ):
        return False
    server.restarts.save(
        key, native_binding={**binding, "native_id": native_id}, native_id_pending=False
    )
    return True
