"""Launch-form contract. Evidence in a probe does not enable a product capability."""

from typing import Any

LAUNCH_REASON = "OpenCode session launch is not available in this DuckTerm build yet."


def snapshot() -> dict[str, Any]:
    # Controlled launch/recovery must be integrated before this becomes true.
    # Do not infer support from an executable on PATH or a successful unit probe.
    return {
        "launch": {"available": False, "reason": LAUNCH_REASON},
        "models": [],
        "model_reason": (
            "No model suggestions are available on this computer. "
            "You can enter a provider/model ID."
        ),
        "capabilities": [
            {"id": identity, "label": label, "available": False, "reason": reason}
            for identity, label, reason in (
                ("launch", "Verified conversation launch", "Not available in this build yet."),
                ("history", "Messages and usage", "Native history integration is not ready yet."),
                ("restart", "Restart and change model", "Use the OpenCode terminal for now."),
                ("approval", "Answer approvals from DuckTerm", "Use the OpenCode terminal."),
                ("fork", "Fork conversation", "Not supported yet."),
                ("priority", "Priority delivery", "Messages remain in the inbox."),
                ("transfer", "Move to remote", "Conversation transfer is not supported yet."),
            )
        ],
    }
