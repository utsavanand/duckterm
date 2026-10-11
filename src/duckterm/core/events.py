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

# Published by the server, never by a hook: the owner attended to a session
# that asked for them (opened it, or answered its note or approval).
ATTENDED = "Attended"
# Published by the server: a fork's merge summary reached the parent agent
# for the first time. The parent's merge checkpoint is taken here, never on
# enqueue, cancel or a failed paste (architect, "Design — Fork merge-back").
MERGE_DELIVERED = "MergeDelivered"
# Published by the server: a session said it needs the owner (publish
# --needs-owner), raising its hand; cleared=True lowers it unless the session
# is waiting for another reason.
NEEDS_OWNER = "NeedsOwner"

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
