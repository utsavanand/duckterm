"""What a run says on the issue it works on: that it started, that it waits
for an approval, that it opened or merged a pull request, and how it ended.

Posted by the DuckTerm server (issue-flow spec §1), never by a flow step.
Comments are public, so they carry no error text (it can name paths on this
Mac); model-written text goes out only where it is the point: triage's
questions and its reason for declining. Approvals are never taken from
comments, and these say so where they ask for one.
"""

from typing import Any

QUESTIONS_CHARS = 3000
REASON_CHARS = 500
FOOTER = "\n\n<sub>DuckTerm autopilot · run `{id}`</sub>"


def message(meta: dict[str, Any], kind: str, detail: str = "") -> str | None:
    """The comment for `kind`, or None when this kind says nothing."""
    pr = meta.get("pr") or {}
    ref = f"{meta.get('repo')}#{pr.get('number')}" if pr else ""
    gate = meta.get("merge_gate") or {}
    texts = {
        "started": (
            "Autopilot started working on this issue: it writes a spec, builds and tests "
            "the fix, and opens a pull request."
        ),
        "spec-gate": (
            "The fix spec needs the owner's approval before any code is written"
            + (f" ({detail})" if detail else "")
            + ". Approvals are given in DuckTerm, not in comments."
        ),
        "merge-gate": (
            f"Pull request {ref} passed CI and waits for the owner's approval to merge"
            + (
                " (every merge is approved during the first runs)"
                if gate.get("shadow")
                else f" ({gate.get('why')})"
                if gate.get("why")
                else ""
            )
            + ". Approvals are given in DuckTerm, not in comments."
        ),
        "pr": f"Opened pull request {ref}.",
        "merged": f"Merged pull request {ref}.",
        "needs_info": (
            "This issue needs more information before a fix can be written:\n\n"
            + detail[:QUESTIONS_CHARS].strip()
            + "\n\nA maintainer can label the issue again once it is answered."
        ),
        "declined": "Autopilot will not work on this issue: " + detail[:REASON_CHARS].strip(),
        "failed": "The run ended without a fix. The owner can see why in DuckTerm.",
        "rejected": "The owner rejected the spec, so the run ended without a fix.",
        "ended": f"The owner did not merge pull request {ref}; it stays open.",
        "cancelled": "The run was cancelled" + (f" ({detail})" if detail else "") + ".",
    }
    text = texts.get(kind)
    return text + FOOTER.format(id=meta.get("id")) if text else None
