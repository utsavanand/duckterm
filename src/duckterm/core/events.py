"""Canonical event-type names, in one place.

These values cross the JSON boundary between the agent's hooks and the server,
so every consumer compares against `Any` — mypy strict gives no protection, and
a typo ("PermissonRequest") produces silent misbehavior (an approval that never
resolves, a state that never flips), not an error. Referencing a constant makes
the typo an AttributeError at import time instead.

Plain string constants, NOT a StrEnum: the values must stay the exact wire
strings the agent emits, and no methods are wanted.
"""

SESSION_START = "SessionStart"
USER_PROMPT_SUBMIT = "UserPromptSubmit"
PRE_TOOL_USE = "PreToolUse"
POST_TOOL_USE = "PostToolUse"
POST_TOOL_USE_FAILURE = "PostToolUseFailure"
PERMISSION_REQUEST = "PermissionRequest"
NOTIFICATION = "Notification"
STOP = "Stop"
SESSION_END = "SessionEnd"
SUBAGENT_START = "SubagentStart"
SUBAGENT_STOP = "SubagentStop"

# The full set an agent's hooks are wired for (order preserved for the installer).
ALL = [
    SESSION_START,
    USER_PROMPT_SUBMIT,
    PRE_TOOL_USE,
    POST_TOOL_USE,
    POST_TOOL_USE_FAILURE,
    PERMISSION_REQUEST,
    NOTIFICATION,
    STOP,
    SESSION_END,
    SUBAGENT_START,
    SUBAGENT_STOP,
]
