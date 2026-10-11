"""Launch-form contract. Evidence in a probe does not enable a product capability."""

import os
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
                ("history", "Messages", "Native history integration is not enabled yet."),
                ("usage", "Aggregate usage", "Native usage is not included in analytics yet."),
                ("restart", "Restart and change model", "Use the OpenCode terminal for now."),
                ("approval", "Answer approvals from DuckTerm", "Use the OpenCode terminal."),
                ("fork", "Fork conversation", "Not supported yet."),
                ("priority", "Priority delivery", "Messages remain in the inbox."),
                ("transfer", "Move to remote", "Conversation transfer is not supported yet."),
            )
        ],
    }


async def launch_options() -> dict[str, Any]:
    from duckterm.harnesses import REGISTRY
    from duckterm.runtimes.opencode_installation import readiness

    result = snapshot()
    # Installing a binary alone never activates an unregistered adapter.
    if "opencode" not in REGISTRY:
        return result
    reason = await readiness(dict(os.environ))
    result["launch"] = {"available": reason is None, **({"reason": reason} if reason else {})}
    for capability in result["capabilities"]:
        if capability["id"] in ("launch", "history"):
            capability.update(
                available=reason is None,
                reason=reason
                or (
                    "Conversation identity is verified before the initial prompt."
                    if capability["id"] == "launch"
                    else "Messages use verified native history; outages retain the last snapshot."
                ),
            )
    return result
