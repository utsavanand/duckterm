"""Owner-thread launch journal in existing restart metadata; no native I/O here.

Claim each native write durably BEFORE sending it. A process restart, timeout,
or lost response must never turn an uncertain write back into an unsent one.
Only exact native evidence advances that claim. Callers do network work off the
owner thread and bring back results bound to the original launch generation.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any

from duckterm.runtimes.opencode_identity import Selection
from duckterm.runtimes.opencode_protocol import OpenCodeError, messages, model_parts, native_id


@dataclass(frozen=True)
class Launch:
    generation: str
    destination: str
    store: str
    directory: str
    pid: int
    prompt: str = ""
    model: str = ""
    phase: str = "prepared"
    session_id: str | None = None
    message_id: str | None = None

    def __post_init__(self) -> None:
        if not all(
            isinstance(x, str) and x for x in (self.generation, self.destination, self.store)
        ):
            raise OpenCodeError("Missing native launch scope")
        if type(self.pid) is not int or self.pid <= 0:
            raise OpenCodeError("Missing native process identity")
        if (
            not isinstance(self.directory, str)
            or not Path(self.directory).is_absolute()
            or str(Path(self.directory).resolve()) != self.directory
        ):
            raise OpenCodeError("Native launch directory must be canonical")
        if not isinstance(self.prompt, str) or len(self.prompt.encode()) > 1024 * 1024:
            raise OpenCodeError("Initial prompt exceeds the launch bound")
        if not isinstance(self.model, str):
            raise OpenCodeError("Invalid requested model")
        if self.model:
            model_parts(self.model)
        if not isinstance(self.phase, str) or self.phase not in {
            "prepared",
            "creating",
            "bound",
            "selecting",
            "submitting",
            "ready",
        }:
            raise OpenCodeError("Unknown native launch phase")
        if self.session_id is not None:
            native_id(self.session_id)
        if self.message_id is not None:
            native_id(self.message_id)
        if self.phase not in {"prepared", "creating"} and self.session_id is None:
            raise OpenCodeError("Native launch is missing its recorded conversation")
        if self.phase in {"prepared", "creating"} and self.session_id is not None:
            raise OpenCodeError("Native launch already has a conversation")

    def require_selection(self, proof: Selection) -> None:
        if (proof.generation, proof.directory, proof.pid, proof.session_id) != (
            self.generation,
            self.directory,
            self.pid,
            self.session_id,
        ):
            raise OpenCodeError("Native selection belongs to another launch")
        proof.require_selected()

    def prompt_body(self) -> dict[str, Any]:
        if not self.prompt or self.message_id is None or self.phase != "submitting":
            raise OpenCodeError("Initial prompt has not been durably claimed")
        body: dict[str, Any] = {
            "messageID": self.message_id,
            "parts": [{"type": "text", "text": self.prompt}],
        }
        if self.model:
            provider, model = model_parts(self.model)
            body["model"] = {"providerID": provider, "modelID": model}
        return body


def _control(conn: sqlite3.Connection, key: str) -> dict[str, Any]:
    row = conn.execute(
        "SELECT runtime, restart_json, state FROM sessions WHERE session_key=?", (key,)
    ).fetchone()
    if row is None or row[0] != "opencode":
        raise OpenCodeError("OpenCode launch no longer owns this card")
    if row[2] in ("stopped", "archived", "merged"):
        raise OpenCodeError("The owner stopped or archived this native launch")
    try:
        control = json.loads(row[1] or "{}")
    except ValueError as exc:
        raise OpenCodeError("Invalid session restart metadata") from exc
    if not isinstance(control, dict):
        raise OpenCodeError("Invalid session restart metadata")
    return control


def _decode(control: dict[str, Any], generation: str) -> Launch:
    try:
        state = Launch(**control["opencode_launch"])
    except (KeyError, TypeError) as exc:
        raise OpenCodeError("Missing or invalid native launch journal") from exc
    if state.generation != generation:
        raise OpenCodeError("Native response belongs to an older launch")
    if bool(state.prompt) != bool(state.message_id):
        raise OpenCodeError("Native launch lost its initial prompt identity")
    if state.session_id is None and control.get("native_binding"):
        raise OpenCodeError("Native conversation binding has changed")
    if state.session_id is not None:
        binding = control.get("native_binding")
        if (
            not isinstance(binding, dict)
            or any(
                binding.get(k) != v
                for k, v in {
                    "runtime": "opencode",
                    "generation": generation,
                    "native_id": state.session_id,
                    "destination": state.destination,
                    "store": state.store,
                }.items()
            )
            or binding.get("contested")
        ):
            raise OpenCodeError("Native conversation binding has changed")
    return state


def read(conn: sqlite3.Connection, key: str, generation: str) -> Launch:
    return _decode(_control(conn, key), generation)


def _save(conn: sqlite3.Connection, key: str, control: dict[str, Any], state: Launch) -> None:
    control["opencode_launch"] = asdict(state)
    conn.execute(
        "UPDATE sessions SET restart_json=? WHERE session_key=?", (json.dumps(control), key)
    )


def begin(conn: sqlite3.Connection, key: str, state: Launch) -> Launch:
    if conn.in_transaction:
        raise OpenCodeError("Native launch requires its own durable transaction")
    if state.phase != "prepared" or state.session_id is not None or state.message_id is not None:
        raise OpenCodeError("Cannot restart an existing native launch as a new one")
    with conn:
        conn.execute("BEGIN IMMEDIATE")
        control = _control(conn, key)
        if "opencode_launch" in control or control.get("native_binding"):
            raise OpenCodeError("This card already has a native launch; reconcile it first")
        state = replace(state, message_id="msg_" + uuid.uuid4().hex if state.prompt else None)
        _save(conn, key, control, state)
    return state


def _change(
    conn: sqlite3.Connection,
    key: str,
    generation: str,
    phase: str,
    change: Callable[[Launch, dict[str, Any]], Launch],
) -> Launch:
    if conn.in_transaction:
        raise OpenCodeError("Native launch requires its own durable transaction")
    with conn:
        conn.execute("BEGIN IMMEDIATE")
        control = _control(conn, key)
        state = _decode(control, generation)
        if state.phase != phase:
            raise OpenCodeError("Native operation already claimed or launch phase changed")
        state = change(state, control)
        _save(conn, key, control, state)
    return state


def claim_create(conn: sqlite3.Connection, key: str, generation: str) -> Launch:
    return _change(conn, key, generation, "prepared", lambda s, _: replace(s, phase="creating"))


def bind_created(conn: sqlite3.Connection, key: str, generation: str, result: Any) -> Launch:
    def bind(state: Launch, control: dict[str, Any]) -> Launch:
        if (
            not isinstance(result, dict)
            or result.get("directory") != state.directory
            or result.get("parentID") is not None
        ):
            raise OpenCodeError("Native creation returned a foreign conversation")
        identity = native_id(result.get("id"))
        for row in conn.execute(
            "SELECT restart_json FROM sessions WHERE runtime='opencode' AND session_key!=?",
            (key,),
        ):
            other = json.loads(row[0] or "{}").get("native_binding") or {}
            if (
                other.get("native_id") == identity
                and other.get("store") == state.store
                and other.get("destination") == state.destination
            ):
                raise OpenCodeError("Native conversation already belongs to another card")
        control["native_binding"] = {
            "runtime": "opencode",
            "native_id": identity,
            "source": "assigned",
            "generation": generation,
            "destination": state.destination,
            "store": state.store,
        }
        control["native_id_pending"] = False
        return replace(state, session_id=identity, phase="bound")

    return _change(conn, key, generation, "creating", bind)


def claim_select(conn: sqlite3.Connection, key: str, generation: str) -> Launch:
    return _change(conn, key, generation, "bound", lambda s, _: replace(s, phase="selecting"))


def claim_prompt(conn: sqlite3.Connection, key: str, generation: str, proof: Selection) -> Launch:
    def selected(state: Launch, _: dict[str, Any]) -> Launch:
        state.require_selection(proof)
        return replace(state, phase="submitting" if state.prompt else "ready")

    # An HTTP selection receipt alone does not advance this phase. After a
    # restart the caller must reacquire a fresh proof, never deserialize one.
    return _change(conn, key, generation, "selecting", selected)


def reconcile_prompt(conn: sqlite3.Connection, key: str, generation: str, result: Any) -> Launch:
    def accepted(state: Launch, _: dict[str, Any]) -> Launch:
        assert state.session_id is not None
        parsed = messages([result], state.session_id)[0]
        info = result["info"]
        text = [b["text"] for b in parsed["blocks"] if b["type"] == "text"]
        if (
            parsed["id"] != state.message_id
            or parsed["role"] != "user"
            or text != [state.prompt]
            or len(parsed["blocks"]) != 1
        ):
            raise OpenCodeError("Native prompt receipt does not match the recorded submission")
        if state.model:
            provider, model = model_parts(state.model)
            if info.get("model") != {"providerID": provider, "modelID": model}:
                raise OpenCodeError("Native prompt receipt uses a different requested model")
        return replace(state, phase="ready")

    # Only an exact read-back is evidence. 204, a missing message, an exception,
    # or a different message leaves submitting intact and never permits resend.
    return _change(conn, key, generation, "submitting", accepted)
