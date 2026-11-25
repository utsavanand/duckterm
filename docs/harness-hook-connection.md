# Harness hook connection

DuckTerm configures Claude Code, Codex, and Copilot hooks before starting their
supervised process. This applies to new sessions, forks, resumes, and harness
changes because they share the supervisor launch path. A restart or harness
change also checks configuration before stopping the current terminal.

The existing installer merges DuckTerm entries into user configuration, retaining
unrelated hooks, permissions, and policy settings. Its locks, atomic writes,
ownership checks, and shared handler remain the installation mechanism. Repeated
launches do not duplicate entries. Malformed settings or unsafe paths stop the
launch with an error rather than silently starting without activity tracking.
Claude's `CLAUDE_CONFIG_DIR` and Codex's `CODEX_HOME` select their configuration
directory when set; the defaults remain unchanged.

Configuration and delivery are separate. The supervisor records configuration
and a launch generation in the session's existing restart metadata. The server
records delivery only after it accepts a matching provider hook from that
generation. Terminal text and synthetic supervisor events do not count. The
evidence survives a DuckTerm server restart and resets on a new agent launch.
Old-generation hooks cannot alter the new session's state or verify it.

The conversation-recovery API returns `hooks.connection.state`: `awaiting` for
the first 30 seconds without evidence, `unconfirmed` afterward, `connected`
after accepted delivery, `inactive` for a stopped session, or `unknown` for
older launches with no connection evidence. No new database table, background
poller, or timeout job is needed; the existing read computes elapsed time.

Installing hooks does not override managed policy, enable disabled hooks, or
grant Codex's hook trust. If the harness blocks execution, delivery remains
unconfirmed. The owner can inspect the harness's `/hooks` view. This is a
configuration and event-path check, not a guarantee that a model is available.

Already-running sessions are not restarted or modified automatically. Their
existing Install hooks action remains available, and their next supervised
launch uses the automatic setup path.

Session details displays Activity tracking independently from conversation identity.
It shows Awaiting first event, Connected, Not confirmed, Not running, or Not verified
from the server's evidence. Existing recovery polling refreshes the state; older
servers without this field do not receive an invented status. First-time setup
says the harness is installed, and explains that activity tracking connects when
a session starts.
