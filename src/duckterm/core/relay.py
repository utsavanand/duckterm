"""Oracle Relay: what needs the owner, shown as notes in the Oracle chat.

Three kinds of note, each tied to one session and one moment:

  approval  a pending permission request (the approval registry)
  choice    Claude's AskUserQuestion menu (arrives as a PermissionRequest)
  question  a turn that ended by asking the owner something (transcript)

The Waiting badge alone is never a note: on 2026-09-26, 5 of 6 Waiting
sessions weren't asking anything. Notes persist in a private file beside the
DB, like the Oracle chat, so answered notes stay in the chat.

Rules follow the Oracle invariant, "the model may propose, never permit":
approval rules match tool/command/folder exactly and only act on blocking
approvals; answer rules only ever send the fixed reply the owner wrote, and
start as drafts the owner sends by hand.
"""

import json
import re
import secrets
from pathlib import Path
from typing import Any

from duckterm.helpers.private_files import private_read, private_write

NOTE_LIMIT = 500
ANSWER_RULE_STREAK = 10  # unchanged sends before an answer rule sends on its own

# Never covered by an approval rule, whatever the owner's wording: these
# can't be undone from a chat message.
IRREVERSIBLE = re.compile(
    r"push\b.*(--force|\s-f\b)|\brm\s+-[a-z]*r[a-z]*f|\bgh\s+release\b|\bnpm\s+publish\b"
    r"|\btwine\s+upload\b|release\.sh|\bgit\s+reset\s+--hard|\bdrop\s+(table|database)\b",
    re.IGNORECASE,
)
# A rule approves one command, not a pipeline: "pytest && curl … | sh" matches
# an anchored "^pytest" pattern, so chained, piped, substituted, or redirected
# commands always go to the owner.
SHELL_COMPOSITION = re.compile(r"[;&|`<>]|\$\(")


# Words that show up when an agent asks the owner for something. A turn ending
# with none of these is skipped without a model call. Measured on 104 labeled
# real turn endings (2026-09-26): passes 21/21 blocking asks and 18/19 offers,
# and skips about a third of all turns.
ASK_CUES = re.compile(
    r"\?|\b(want me to|should i|shall i|do you want|would you like|let me know|tell me"
    r"|say (the word|go|so|when|where|yes)|your (call|word|review|decision|move)|on your word"
    r"|when you'?re ready|ready (to|when|for)|reply|confirm|approve|which (one|do you|would|should)"
    r"|prefer|decide|decision|waiting (on|for) you|need(s)? (you|your)|i need|can you|could you"
    r"|please|go ahead|next (move|step) is yours|review|preview|sign (in|into)|options?\b|choice"
    r"|anything else|or (should|shall|would) |(then|so|before) i can|once you|i recommend"
    r"|recommendation|up to you)",
    re.IGNORECASE,
)

# The classifier Oracle runs on a turn ending that passed ASK_CUES. On the same
# 104 endings, with Sonnet, over three runs: "blocked" was right 94% of the time
# and caught 71-81% of blocking asks (runs vary on borderline endings); counting
# offers too, 86-88% right and 75-78% caught. The question-mark check it
# replaces caught 10% of blocking asks. Haiku over-flagged (52% right).
# Adding hand-off examples ("next move is yours") dropped precision to 80%
# without fixing that miss, so they were left out.
ASK_PROMPT = """You read how an AI coding agent ended its turn: its final message, and the \
owner's message that started the turn. Decide whether the agent is now waiting on the owner. \
Default to none: most turns end with a report.

- blocked: the agent explicitly waits on the owner before the task can go on. It asks the \
owner to pick between options it listed, to confirm scope or an interpretation before it \
builds, to supply a key, file, or account, or to do something only the owner can (sign in, \
add a member, save a file and reply). A ready-and-waiting gate on a consequential step also \
counts: "ready to merge to main on your word", "say go and I'll deploy", "say the word when \
you want it live", "approve this?".
- offer: the work is done and the agent directly asks whether to do an optional next thing: \
"Want me to also...?", "Should I do X next or Y?", "Anything else, or shall we move on?".
- none: everything else. Status and results ("Deployed; I'll confirm", "Pushed to GitHub"), \
explanations and answers, instructions or review feedback for the owner to act on alone \
("Fix those and rerun", "Refresh the tab"), recommendations without a direct ask, the agent \
carrying on by itself ("I'll assume yes unless...", "Let me also add..."), soft courtesies \
("if it still feels off, say where", "if you meant X, say so", "say the word if you'd \
rather"), errors, and greetings like "What would you like to work on?".

Judge the words, not punctuation: many asks have no question mark, and many question marks \
are rhetorical.
Return STRICT JSON only: {"kind": "blocked" | "offer" | "none", "ask": "<what the agent \
needs, one short sentence to the owner; empty for none>", "options": ["<short label>", ...]}
options: only when the agent lists distinct choices for the owner to pick; otherwise [].

OWNER'S MESSAGE:
<<PROMPT>>

AGENT'S FINAL MESSAGE (end of turn):
<<FINAL>>
"""


def ask_prompt(owner_message: str, final: str) -> str:
    return ASK_PROMPT.replace("<<PROMPT>>", owner_message[:800]).replace("<<FINAL>>", final[-2500:])


def parse_ask(text: str) -> dict[str, Any] | None:
    """The classifier's verdict as {kind, ask, options}, or None when unusable."""
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match is None:
        return None
    try:
        raw = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    if not isinstance(raw, dict) or raw.get("kind") not in ("blocked", "offer", "none"):
        return None
    listed = raw.get("options")
    options: list[Any] = listed if isinstance(listed, list) else []
    return {
        "kind": raw["kind"],
        "ask": str(raw.get("ask") or "").strip()[:300],
        "options": [str(o).strip()[:60] for o in options if str(o).strip()][:6],
    }


def question_from(text: str) -> str | None:
    """Fallback when no model is available: the last paragraph, if it ends
    with "?". It catches few real asks (10% of blocking ones on the labeled
    set), so notes from it say they were detected without the model."""
    prose = re.sub(r"```.*?```", "", text, flags=re.DOTALL)
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", prose) if p.strip()]
    if not paragraphs:
        return None
    last = paragraphs[-1]
    plain = re.sub(r"[*_`#>]+", "", last).strip()
    if not plain.endswith("?"):
        return None
    return last[-600:]


def choice_from(tool_input: dict[str, Any]) -> tuple[str, list[str]] | None:
    """(question, option labels) from an AskUserQuestion call's input."""
    questions = tool_input.get("questions")
    if not isinstance(questions, list) or not questions or not isinstance(questions[0], dict):
        return None
    q = questions[0]
    options = [
        str(o.get("label"))
        for o in q.get("options") or []
        if isinstance(o, dict) and o.get("label")
    ]
    if not q.get("question") or not options:
        return None
    return str(q["question"]), options


class Relay:
    def __init__(self, path: Path) -> None:
        self.path = path
        raw = private_read(path)
        data = json.loads(raw) if raw else {}
        self.notes: list[dict[str, Any]] = data.get("notes", []) if isinstance(data, dict) else []
        self.rules: list[dict[str, Any]] = data.get("rules", []) if isinstance(data, dict) else []

    def _save(self) -> None:
        self.notes = self.notes[-NOTE_LIMIT:]
        private_write(self.path, json.dumps({"notes": self.notes, "rules": self.rules}))

    def open_notes(self) -> list[dict[str, Any]]:
        return [n for n in self.notes if n["status"] == "open"]

    def needs_you(self) -> list[dict[str, Any]]:
        """Open notes that wait on the owner. Offers show in the chat but
        don't count: the agent finished and is only suggesting more."""
        return [n for n in self.open_notes() if n.get("urgency") != "offer"]

    def get(self, note_id: str) -> dict[str, Any] | None:
        return next((n for n in self.notes if n["id"] == note_id), None)

    def add(self, note: dict[str, Any]) -> dict[str, Any]:
        note = {"id": "n-" + secrets.token_hex(6), "status": "open", **note}
        self.notes.append(note)
        self._save()
        return note

    def update(self, note: dict[str, Any], **fields: Any) -> None:
        note.update(**fields)
        self._save()

    def close(self, note: dict[str, Any], status: str, **fields: Any) -> None:
        self.update(note, status=status, **fields)

    def close_for_session(self, session_key: str, kinds: set[str], status: str, at: int) -> None:
        changed = False
        for n in self.open_notes():
            if n["session_key"] == session_key and n["kind"] in kinds:
                n.update(status=status, closed_at=at)
                changed = True
        if changed:
            self._save()

    # ── rules ──

    def add_rule(self, rule: dict[str, Any], now: int) -> dict[str, Any]:
        number = 1 + max((int(r["id"][1:]) for r in self.rules), default=0)
        rule = {**rule, "id": f"R{number}", "created_at": now}
        if rule["kind"] == "answer":
            rule.update(mode="draft", streak=0)
        self.rules.append(rule)
        self._save()
        return rule

    def delete_rule(self, rule_id: str) -> bool:
        before = len(self.rules)
        self.rules = [r for r in self.rules if r["id"].lower() != rule_id.lower()]
        self._save()
        return len(self.rules) < before

    def approval_rule_for(self, tool: str, detail: str, folder: str) -> dict[str, Any] | None:
        """The first approval rule that matches exactly, or None. Irreversible
        commands never match."""
        if IRREVERSIBLE.search(detail) or SHELL_COMPOSITION.search(detail):
            return None
        for r in self.rules:
            if r["kind"] != "approval":
                continue
            if r.get("tool") and r["tool"] != tool:
                continue
            if r.get("folder") and not (
                folder == r["folder"] or folder.startswith(r["folder"] + "/")
            ):
                continue
            if r.get("command_pattern") and not re.search(r["command_pattern"], detail):
                continue
            return r
        return None

    def answer_rule_for(self, question: str) -> dict[str, Any] | None:
        """The first answer rule whose keywords all appear in the question."""
        text = question.lower()
        for r in self.rules:
            if (
                r["kind"] == "answer"
                and r.get("keywords")
                and all(k.lower() in text for k in r["keywords"])
            ):
                return r
        return None

    def record_send(self, rule_id: str, unchanged: bool) -> None:
        """An owner send of a drafted reply: unchanged sends build trust, any
        edit or different answer resets it."""
        for r in self.rules:
            if r["id"] == rule_id and r["kind"] == "answer":
                r["streak"] = r.get("streak", 0) + 1 if unchanged else 0
                if r["streak"] >= ANSWER_RULE_STREAK:
                    r["mode"] = "live"
                self._save()


def validate_rule(raw: object) -> dict[str, Any]:
    """A rule proposal from the model or the client, checked and normalized.
    Raises ValueError with a reason the owner can read."""
    if not isinstance(raw, dict):
        raise ValueError("A rule must be an object")
    kind = raw.get("kind")
    summary = str(raw.get("summary") or "").strip()[:200]
    if kind == "approval":
        action = raw.get("action")
        if action not in ("approve", "deny"):
            raise ValueError("An approval rule must approve or deny")
        pattern = str(raw.get("command_pattern") or "")
        try:
            re.compile(pattern)
        except re.error as exc:
            raise ValueError(f"The command pattern isn't valid: {exc}") from exc
        if not pattern and not raw.get("tool"):
            raise ValueError("An approval rule needs a tool or a command pattern")
        return {
            "kind": "approval",
            "summary": summary,
            "tool": str(raw.get("tool") or "") or None,
            "command_pattern": pattern or None,
            "folder": str(raw.get("folder") or "") or None,
            "action": action,
        }
    if kind == "answer":
        keywords = [str(k).strip() for k in raw.get("keywords") or [] if str(k).strip()]
        reply = str(raw.get("reply") or "").strip()
        if not keywords or not reply:
            raise ValueError("An answer rule needs keywords to match and a reply to send")
        return {
            "kind": "answer",
            "summary": summary,
            "keywords": keywords[:6],
            "reply": reply[:2000],
        }
    raise ValueError("A rule is either an approval rule or an answer rule")


RULE_PROMPT = """Turn the owner's instruction into one rule for DuckTerm's Oracle,
or say it isn't a rule. Return STRICT JSON only, one of these shapes:
{"kind": "approval", "summary": "...", "tool": "Bash" | null,
 "command_pattern": "<Python regex>" | null,
 "folder": "<top-level sidebar folder>" | null, "action": "approve" | "deny"}
{"kind": "answer", "summary": "...", "keywords": ["...", "..."], "reply": "..."}
{"not_a_rule": true}

- approval: an agent asks permission to run a tool. command_pattern matches the
  command text; use \\b and anchors sensibly, e.g. "pytest" becomes
  "^(\\.venv/bin/)?(python -m )?pytest\\b".
- answer: an agent ends its turn asking the owner a question. keywords are 1-4
  short lowercase words that must ALL appear in such a question. reply is
  exactly what to send.
- summary: one short sentence in plain words describing the rule.
Known folders: {folders}

Instruction: {text}
"""


def parse_rule_reply(text: str) -> dict[str, Any] | None:
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match is None:
        return None
    try:
        raw = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    if not isinstance(raw, dict) or raw.get("not_a_rule"):
        return None
    return raw
