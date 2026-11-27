#!/usr/bin/env python3
"""Skill-kit hook shim (spec §3.5, §III.6). Written into the project by the
installer; a bare clone runs it without DuckTerm. Standard library only, and
it must never import duckterm: the installer copies this file as it is.

    python3 _skill_kit_shim.py <harness> <event> <kit> <script>

Reads the harness's own hook JSON from stdin, rewrites it to the neutral
payload, pipes that to the kit's script, and maps the script's result back:
exit 0 allows; exit 2 blocks (with the reason on stderr); an optional single
JSON line on stdout {"decision": "allow|block", "reason": …, "context": …}
is honoured too. Everything the shim has no mapping for stays under `raw`.
Claude Code, Codex and Copilot run it as a command hook; OpenCode runs it
from the plugin DuckTerm generates, which throws on exit 2.
"""

import json
import os
import subprocess
import sys
from typing import Any

CLAUDE_TOOLS = {
    "Bash": "shell",
    "Read": "read",
    "Write": "write",
    "Edit": "edit",
    "MultiEdit": "edit",
    "NotebookEdit": "edit",
    "Grep": "search",
    "Glob": "search",
}
COPILOT_TOOLS = {
    "bash": "shell",
    "shell": "shell",
    "powershell": "shell",
    "read": "read",
    "view": "read",
    "write": "write",
    "create": "write",
    "edit": "edit",
    "grep": "search",
    "glob": "search",
    "patch": "edit",
}
CLAUDE_EVENT = {
    "session_start": "SessionStart",
    "session_end": "SessionEnd",
    "prompt_submit": "UserPromptSubmit",
    "before_tool": "PreToolUse",
    "after_tool": "PostToolUse",
    "tool_failed": "PostToolUseFailure",
    "permission_request": "PermissionRequest",
    "notification": "Notification",
    "stop": "Stop",
    "subagent_start": "SubagentStart",
    "subagent_stop": "SubagentStop",
    "before_compact": "PreCompact",
}
COPILOT_EXIT2 = ("before_tool", "permission_request")
COPILOT_JSON = ("stop", "subagent_stop")
OPENCODE_EXIT2 = ("before_tool",)  # the plugin throws on exit 2, aborting the tool call


def normalise_input(name: str, params: dict[str, Any]) -> dict[str, Any]:
    if name == "shell":
        return {"command": params.get("command") or params.get("cmd") or ""}
    if name in ("read", "write", "edit"):
        path = params.get("file_path") or params.get("path") or params.get("filePath") or ""
        out: dict[str, Any] = {"path": path}
        if name == "write" and "content" in params:
            out["content"] = params["content"]
        return out
    if name == "search":
        return {"pattern": params.get("pattern") or params.get("query") or ""}
    return params


def _parse_args(value: Any) -> dict[str, Any]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return {"raw": value}
    return value if isinstance(value, dict) else {}


def normalise(
    harness: str, event: str, kit: str, payload: dict[str, Any], cwd: str
) -> dict[str, Any]:
    tool = None
    if harness in ("claude-code", "codex"):
        raw_name = payload.get("tool_name")
        if raw_name:
            name = CLAUDE_TOOLS.get(str(raw_name), str(raw_name))
            tool = {"name": name, "input": normalise_input(name, payload.get("tool_input") or {})}
        prompt = payload.get("prompt")
        result = payload.get("tool_response")
        session = payload.get("session_id")
        version = payload.get("version") or payload.get("harness_version")
    else:  # copilot, or the OpenCode plugin sending the same keys
        raw_name = payload.get("toolName") or payload.get("tool_name")
        if raw_name:
            name = COPILOT_TOOLS.get(str(raw_name).lower(), str(raw_name))
            params = _parse_args(payload.get("toolArgs") or payload.get("tool_input") or {})
            tool = {"name": name, "input": normalise_input(name, params)}
        prompt = payload.get("prompt")
        result = payload.get("toolResult") or payload.get("tool_response")
        session = payload.get("sessionId") or payload.get("session_id")
        version = payload.get("version")
    return {
        "event": event,
        "harness": harness,
        "harness_version": version,
        "session": session,
        "cwd": cwd,
        "kit": kit,
        "tool": tool,
        "prompt": prompt if event == "prompt_submit" else None,
        "result": result if event in ("after_tool", "tool_failed") else None,
        "raw": payload,
    }


def map_result(
    harness: str, event: str, exit_code: int, stdout: str, stderr: str
) -> tuple[int, str, str]:
    """→ (exit code for the harness, stdout text, stderr text)."""
    decision = "block" if exit_code == 2 else "allow"
    reason, context = "", ""
    line = stdout.strip().splitlines()[-1] if stdout.strip() else ""
    if line.startswith("{"):
        try:
            data = json.loads(line)
        except ValueError:
            data = {}
        if isinstance(data, dict):
            decision = str(data.get("decision") or decision)
            reason = str(data.get("reason") or "")
            context = str(data.get("context") or "")
    reason = reason or stderr.strip()
    if exit_code not in (0, 2):
        # exit 1 never blocks anywhere; surface the failure without stopping the agent
        return 0, "", stderr
    if decision == "block":
        if (
            harness in ("claude-code", "codex")
            or (harness == "copilot" and event in COPILOT_EXIT2)
            or (harness == "opencode" and event in OPENCODE_EXIT2)
        ):
            return 2, "", reason
        if harness == "copilot" and event in COPILOT_JSON:
            return 0, json.dumps({"decision": "block", "reason": reason}), ""
        return 0, "", "skill-kit: " + reason + " (this event cannot block in " + harness + ")"
    if context and harness in ("claude-code", "codex"):
        output = {
            "hookSpecificOutput": {
                "hookEventName": CLAUDE_EVENT.get(event, event),
                "additionalContext": context,
            }
        }
        return 0, json.dumps(output), ""
    return 0, "", ""


def main(argv: list[str]) -> int:
    if len(argv) != 5:
        sys.stderr.write("usage: _skill_kit_shim.py <harness> <event> <kit> <script>\n")
        return 1
    harness, event, kit, script = argv[1:]
    cwd = os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()
    raw = sys.stdin.read()
    payload: Any
    try:
        payload = json.loads(raw) if raw.strip() else {}
    except ValueError:
        payload = {"unparsed": raw}
    if not isinstance(payload, dict):
        payload = {"unparsed": payload}
    neutral = normalise(harness, event, kit, payload, cwd)
    try:
        proc = subprocess.run(
            [script if os.path.isabs(script) else os.path.join(cwd, script)],
            input=json.dumps(neutral),
            capture_output=True,
            text=True,
            cwd=cwd,
            timeout=float(os.environ.get("SKILL_KIT_HOOK_TIMEOUT", "60")),
        )
        code, out, err = proc.returncode, proc.stdout, proc.stderr
    except (OSError, subprocess.TimeoutExpired) as exc:
        code, out, err = 1, "", f"skill-kit hook failed to run: {exc}"
    code, out, err = map_result(harness, event, code, out, err)
    if out:
        sys.stdout.write(out)
    if err:
        sys.stderr.write(err)
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv))
