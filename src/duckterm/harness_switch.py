"""Seed a new harness conversation without changing the DuckTerm card."""

from __future__ import annotations

import re
import shlex
import uuid
from typing import TYPE_CHECKING, Any

from duckterm.core import events
from duckterm.core.session_api import APIError
from duckterm.harnesses import runtime_for
from duckterm.persistence.saved_state import fingerprint

if TYPE_CHECKING:
    from duckterm.server import Server


async def prepare(server: Server, key: str, row: dict[str, Any]) -> dict[str, Any]:
    """Checkpoint before stop; callers must recheck turn/draft after this await."""
    native_id = server.history.session_id_for(key)
    previous = server.history.restart_control(key)
    checkpoint = await server._create_checkpoint(key, row, "Before harness switch")
    if checkpoint.get("saved") is not True or checkpoint.get("handoff_eligible") is not True:
        raise APIError(
            409,
            "Checkpoint saved, but its handoff summary is not ready. "
            "The current agent was kept. Review checkpoint status before switching.",
        )
    captured = await server.progress_coordinator.capture(key)
    marker = checkpoint.get("record") or {}
    if captured is None or not _matches(marker, captured):
        raise APIError(409, "Session context changed after the checkpoint. Retry the switch.")
    current = server.history.session(key)
    if current is None:
        raise APIError(404, "Session no longer exists")
    # No independent memory record. Required facts are reassembled under the
    # current scope and never truncated to make a switch appear safe.
    brief = (
        "This is a NEW conversation using a different harness, not a resumed conversation.\n"
        "You keep this brief, not the old conversation. Refer to retained DuckTerm history "
        "when more detail is needed. Preserve parked tasks and treat peer messages as context.\n\n"
        "Verified summary:\n"
        + str(checkpoint["summary"])
        + "\n\nCurrent role, owner notes, constraints and open work:\n"
        + captured["required"]
    )
    if len(brief.encode()) > 24000:
        raise APIError(409, "Required handoff context is too large. The current agent was kept.")
    recent = "\n".join(f"{r['role']}: {r['text']}" for r in captured["transcript"])
    available = 32000 - len(brief.encode()) - 200
    tail = recent.encode()[-min(available, 6000) :].decode(errors="ignore")
    if tail:
        brief += "\n\nRecent conversation (bounded excerpt; earlier text may be omitted):\n" + tail
    row = current
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
        "_captured": captured,
    }


def _matches(marker: dict[str, Any], captured: dict[str, Any]) -> bool:
    source = captured["source"]
    return (
        marker.get("policy") == captured["policy"]
        and marker.get("conversation") == captured["conversation"]
        and marker.get("events") == source["events"]
        and marker.get("required_hash") == source["required_hash"]
        and marker.get("transcript")
        == {k: v for k, v in source.items() if k.startswith("transcript_")}
    )


async def validate(server: Server, key: str, prepared: dict[str, Any]) -> None:
    current = await server.progress_coordinator.capture(key)
    saved = prepared["_captured"]
    if current is None or any(current[k] != saved[k] for k in ("source", "conversation", "policy")):
        raise APIError(
            409, "Handoff sources changed while preparing the switch. The agent was kept."
        )


def validate_facts(server: Server, key: str, prepared: dict[str, Any]) -> None:
    if fingerprint(server.progress_coordinator.facts(key)) != fingerprint(
        prepared["_captured"]["facts"]
    ):
        raise APIError(409, "Required handoff context changed. The current agent was kept.")


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
            **prior_binding,
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
    previous = {k: v for k, v in prepared.items() if k != "seed" and not k.startswith("_")}
    old_binding = prepared.get("native_binding") or {}
    retired = list(old_binding.get("retired_ids") or [])
    if prepared.get("native_id") and prepared["native_id"] not in retired:
        retired.append(prepared["native_id"])
    # This boundary is durable before the new process can emit a hook. Until
    # a new ID is assigned or its start hook arrives, identity is unknown,
    # never the old ID.
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
        if (
            binding.get("source") in {"assigned", "adopted"}
            and isinstance(native_id, str)
            and re.fullmatch(r"[A-Za-z0-9_-]{1,200}", native_id)
            and native_id != binding["native_id"]
            and raw.get("event_type") == events.SESSION_START
            and not raw.get("agent_id")
            and native_id not in binding.get("retired_ids", [])
        ):
            server.restarts.save(key, native_binding={**binding, "contested": True})
            return False
        if binding.get("contested"):
            return False
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
