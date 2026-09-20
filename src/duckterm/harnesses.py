"""The harness registry: the single source of truth for which agents Duckterm
supports. Each entry is one Harness adapter (in runtimes/) that owns both halves
of an agent's integration:

  - drive   — launch/resume, state detection, transcript reading.
  - observe — its `hook_spec`: where its hook config lives and how to merge/strip
              our entries (None for agents with no hook system).

Onboarding a new agent = implement the Harness contract once and add one entry
here. Everything that asks "what agents exist" (the CLI's --agent choices, the
dashboard agent picker via the server, runtime construction, hook install,
transcript reading for checkpoints) resolves through this registry.
"""

from duckterm.runtimes.base import AgentRuntime, Harness
from duckterm.runtimes.claude_code import ClaudeCodeRuntime
from duckterm.runtimes.codex import CodexRuntime
from duckterm.runtimes.copilot import CopilotRuntime
from duckterm.runtimes.generic import GenericRuntime

# name -> adapter class. The class is the factory (called with a command) and
# carries the agent's hook_spec as a class attribute, so installable_agents can
# filter without instantiating.
REGISTRY: dict[str, type[Harness]] = {
    "claude-code": ClaudeCodeRuntime,
    "codex": CodexRuntime,
    "copilot": CopilotRuntime,
    # The lowest-common-denominator: any CLI agent, driven only (no hooks, so
    # not watchable). Not offered as an install-hooks choice.
    "generic": GenericRuntime,
}


def runtime_for(name: str | None, command: str) -> AgentRuntime:
    """Build the drive-adapter for `name` (falling back to generic)."""
    cls = REGISTRY.get(name or "", GenericRuntime)
    return cls(command)


def infer_runtime(command: str) -> str:
    """Guess the runtime name from a command's first word, so callers needn't
    pass one — `claude …` -> claude-code, `codex …` -> codex, else generic.
    Lives here beside the registry (the source of truth for agent names) so the
    CLI can use it without importing the whole server."""
    first = (command.strip().split() or [""])[0].rsplit("/", 1)[-1]
    if first.startswith("claude"):
        return "claude-code"
    if first.startswith("codex"):
        return "codex"
    if first.startswith("copilot"):
        return "copilot"
    return "generic"


def installable_agents() -> list[str]:
    """Agents that can be wired for watched sessions (have a hook adapter)."""
    return [name for name, cls in REGISTRY.items() if cls.hook_spec is not None]
