"""The review chat (spec §6): questions answered from the PR, its diff, the
cards so far and the authoring session's digest, with path:line citations.
The transcript is a JSON file beside the cards; it is not a session."""

from __future__ import annotations

import json
import re
import time
from typing import Any

from duckterm import memory_provider
from duckterm.core.session_api import APIError
from duckterm.review import cards, git, store
from duckterm.review.workspace import Target

FILE = "chat.json"
MAX_FILES = 5
MAX_FILE_CHARS = 20000
CITATION = re.compile(r"(?<![\w/])((?:[\w.-]+/)*[\w.-]+\.[A-Za-z0-9]{1,6}):(\d+)")

SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["answer", "citations", "needs_files"],
    "properties": {
        "answer": {"type": "string"},
        "citations": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["path", "line"],
                "properties": {"path": {"type": "string"}, "line": {"type": "integer"}},
            },
        },
        "needs_files": {"type": "array", "items": {"type": "string"}},
    },
}

SUGGESTIONS = [
    "What is the riskiest hunk and why?",
    "Which claims in the description are not in the diff?",
    "What should I test by hand before merging?",
    "Draft a request-changes review from the open questions",
]


def transcript(target: Target) -> list[dict[str, Any]]:
    value = store.load(target.directory, FILE).get("messages")
    return list(value) if isinstance(value, list) else []


def _append(target: Target, entry: dict[str, Any]) -> None:
    with store._lock:
        messages = transcript(target)
        messages.append(entry)
        store.save(target.directory, FILE, {"messages": messages[-200:]})


def _file_text(target: Target, path: str) -> str | None:
    try:
        text = git.run(target.repo, "show", f"{target.head_ref}:{path}", timeout=20)
    except git.ReviewGitError:
        return None
    return text[:MAX_FILE_CHARS]


def _prompt(
    target: Target,
    ctx: dict[str, Any],
    ready: dict[str, Any],
    digest: str | None,
    history: list[dict[str, Any]],
    question: str,
    hunk: str | None,
    files: dict[str, str],
) -> str:
    parts = [
        cards.EVIDENCE,
        f"# Pull request\nTitle: {ctx['title']}\nBranch: {target.branch} → {target.base} "
        f"(base {target.base_sha[:7]}, head {target.head_sha[:7]})\n\n"
        f"Description:\n{ctx['body'] or '(none)'}",
        "# Diff\n" + ctx["diff"],
    ]
    if ready:
        parts.append("# Review cards so far\n" + json.dumps(ready, indent=1)[:30000])
    if digest:
        parts.append("# The authoring session's latest digest\n" + digest[:6000])
    if files:
        parts.append(
            "# Files you asked for (head revision)\n"
            + "\n\n".join(f"### {path}\n```\n{text}\n```" for path, text in files.items())
        )
    if history:
        parts.append(
            "# Earlier in this conversation\n"
            + "\n\n".join(f"Q: {m['q']}\nA: {m['a']}" for m in history[-6:])
        )
    ask = f"# Question\n{question}"
    if hunk:
        ask += f"\n\nThe reviewer is looking at: {hunk}"
    parts.append(ask)
    parts.append(
        "Answer in markdown for a reviewer, concrete and short. Cite evidence as path:line using "
        "paths from the diff and line numbers from the head revision. If you need the full text of "
        "a changed file to answer, list its path in needs_files (at most 5) and give a provisional "
        "answer; otherwise needs_files is empty.\n\nReply with one JSON object matching this "
        f"schema and nothing else:\n{json.dumps(SCHEMA)}"
    )
    return "\n\n".join(parts)


async def ask(
    target: Target,
    payload: dict[str, Any],
    question: str,
    *,
    hunk: str | None,
    digest: str | None,
) -> dict[str, Any]:
    provider = cards.provider()
    if provider is None:
        raise APIError(503, "No Claude Code or Codex CLI is installed to answer")
    harness, model = provider
    ctx = cards.context(target, payload)
    ready = {c["id"]: c["data"] for c in cards.load(target)["cards"] if c.get("status") == "ready"}
    history = transcript(target)
    files: dict[str, str] = {}
    value: dict[str, Any] | None = None
    for _round in range(2):
        prompt = _prompt(target, ctx, ready, digest, history, question, hunk, files)
        reply = await memory_provider.generate(harness, model, prompt, SCHEMA, user_settings=True)
        value = cards._parse_reply(reply)
        if value is None or "answer" not in value:
            raise APIError(502, "The model did not return an answer")
        wanted = [
            p for p in value.get("needs_files") or [] if isinstance(p, str) and p in ctx["paths"]
        ]
        if not wanted or files:
            break
        for path in wanted[:MAX_FILES]:
            text = _file_text(target, path)
            if text is not None:
                files[path] = text
        if not files:
            break
    assert value is not None
    known = set(ctx["paths"])
    citations = [
        {"path": c["path"], "line": int(c["line"])}
        for c in value.get("citations") or []
        if isinstance(c, dict) and c.get("path") in known and isinstance(c.get("line"), int)
    ]
    for match in CITATION.finditer(str(value["answer"])):
        if match[1] in known and not any(
            c["path"] == match[1] and c["line"] == int(match[2]) for c in citations
        ):
            citations.append({"path": match[1], "line": int(match[2])})
    entry = {
        "q": question,
        "a": str(value["answer"]),
        "citations": citations,
        "hunk": hunk,
        "files": sorted(files),
        "at": int(time.time()),
    }
    _append(target, entry)
    return entry


def draft_review(target: Target) -> str:
    """A request-changes body from card 10, grouped by kind. Deterministic."""
    loaded = cards.load(target)
    questions = next((c for c in loaded["cards"] if c["id"] == "questions"), None)
    if not questions or questions.get("status") != "ready":
        raise APIError(409, "Open questions card is not ready yet")
    items = questions["data"].get("items", [])
    titles = {
        "unverified": "Not shown in the diff",
        "confirm": "Please confirm",
        "cleanup": "Before merge",
        "manual": "Needs a hand test",
    }
    lines = [f"Review of {target.branch} at {target.head_sha[:7]} from DuckTerm.", ""]
    for kind, title in titles.items():
        rows = [i for i in items if i.get("kind") == kind]
        if not rows:
            continue
        lines.append(f"**{title}**")
        for row in rows:
            paths = f" ({', '.join(row['paths'])})" if row.get("paths") else ""
            lines.append(f"- {row['text']}{paths}")
        lines.append("")
    return "\n".join(lines).strip()


def record_sent(target: Target, card: str, session: str, message_id: str) -> None:
    with store._lock:
        state = store.load(target.directory, cards.FILE)
        entry = (state.get("cards") or {}).get(card)
        if not entry:
            return
        entry.setdefault("sent", []).append(
            {"session": session, "message_id": message_id, "at": int(time.time())}
        )
        store.save(target.directory, cards.FILE, state)
