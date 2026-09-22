# Inbox persistence and notices

Assignments have no deadline by default. They remain queued or accepted until
answered, declined, or cancelled. Senders may set an explicit deadline with
`duckterm session ask --timeout SECONDS` (up to seven days). Accepting an
explicitly timed question does not remove its deadline. Closed records retain
the normal seven-day cleanup policy. Existing expired records are not reopened.

Stop/resume preserves pending requests. Deleting a session still cancels its work.
Persistent deadlines are stored as a far-future timestamp for compatibility with
older servers; the API exposes them as `expires_at: 0`.

## Task-end notices

Claude Code's synchronous Stop hook can return one line of additional context.
RubberTerm uses this to mention accepted, unanswered assignments once per record.
A new accepted assignment makes another notice eligible. Brand-new peer questions
wait for the agent's normal inbox check. Notices neither accept nor answer work.

The server checks the state before processing Stop, suppresses notices while
waiting for a human or approval, and honors the runtime's stop-hook loop guard.
A per-harness capability defaults to unsupported. Codex, Copilot, and generic
runtimes currently have no automatic task-end notice. Already-idle sessions are
not awakened: there is no terminal injection or background polling worker.

The hook POST is bounded to two seconds and fails quietly if the server is down.
Delivery metadata records when a notice was offered, not proof that the agent
read it. Agent inbox reads record a separate timestamp; dashboard reads do not.
A dropped response may lose a notice, but never the underlying assignment.

New sessions receive instructions to check their inbox at idle-turn starts and
suitable pauses. Existing Claude sessions may need their hooks reinstalled and
a restart before the synchronous Stop configuration takes effect.

The owner-broadcast backend described in `inbox-awareness-design.md` is separate
follow-up work; this change covers peer assignments.

Runtime contract: [Claude Code Stop hooks](https://code.claude.com/docs/en/hooks#stop-decision-control).
