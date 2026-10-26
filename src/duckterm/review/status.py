"""The status of one branch or pull request, derived from git and GitHub
facts. Pure: every input is a value, so the table in the spec is testable."""

from __future__ import annotations

from typing import Any

LABELS = {
    "unpushed": "Not pushed",
    "pushed": "Pushed, no PR",
    "draft": "Draft",
    "open": "Open · awaiting review",
    "changes_requested": "Open · changes requested",
    "ci_failing": "CI failing",
    "conflict": "Conflict with base",
    "ready": "Ready",
    "merged": "Merged",
    "closed": "Closed",
}


def derive(
    pr: dict[str, Any] | None,
    *,
    pushed: bool,
    conflict: bool | None,
    checks: dict[str, int] | None,
    review: str = "none",
    version: str | None = None,
) -> dict[str, Any]:
    if pr is None:
        status = "pushed" if pushed else "unpushed"
        return {"status": status, "label": LABELS[status], "needs_you": False}
    if pr["state"] == "merged":
        label = f"Merged · {version}" if version else LABELS["merged"]
        return {"status": "merged", "label": label, "needs_you": False}
    if pr["state"] == "closed":
        return {"status": "closed", "label": LABELS["closed"], "needs_you": False}
    if pr["draft"]:
        return {"status": "draft", "label": LABELS["draft"], "needs_you": False}
    failing = bool(checks and checks["failed"])
    if conflict:
        return {"status": "conflict", "label": LABELS["conflict"], "needs_you": True}
    if failing:
        return {"status": "ci_failing", "label": LABELS["ci_failing"], "needs_you": True}
    if review == "changes_requested":
        status = "changes_requested"
        return {"status": status, "label": LABELS[status], "needs_you": False}
    pending = bool(checks and checks["pending"])
    if review == "approved" and conflict is False and not pending:
        return {"status": "ready", "label": LABELS["ready"], "needs_you": True}
    return {"status": "open", "label": LABELS["open"], "needs_you": True}
