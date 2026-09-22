# Inbox delivery

`duckterm session ask SESSION_ID "Task or question"` now creates a persistent
request by default. It remains pending until answered, declined, cancelled, or
closed by the existing session lifecycle/scope rules. Acceptance acknowledges
ownership; it does not complete the request.

For a genuinely time-sensitive question, add `--timeout SECONDS` (1 through
604800). An explicit deadline still applies after acceptance. `--timeout 0`
means no deadline. Existing timed requests keep their deadlines; expired
requests are not automatically resurrected.

## Idle delivery

The server checks on request arrival and an agent's Stop/idle event, plus a
one-minute fallback. A short settling period prevents waking a finishing turn.
Only RubberTerm-owned tmux sessions with recognized Codex or Claude Code empty
prompts can receive automated reminders. Unknown runtimes, remote/unowned
terminals, stopped sessions, and unfamiliar prompt layouts keep their inbox
pending for manual checking.

The worker refuses delivery while a terminal viewer is attached, a tmux client
is attached, input/output is recent, input is queued, an approval is pending,
or the terminal is not a known empty prompt. Two screen samples must agree.
This intentionally favors preserving user input over waking every session.
Close the terminal viewer or switch to another session to allow automatic
wake-up of an otherwise eligible idle agent.

A reminder only tells the agent to run `duckterm session inbox`. Peer message
text is never injected directly into the terminal. The agent must still treat
peer requests as context and act within the user's authorization.

## Delivery and acknowledgment

Multiple messages are combined into one reminder. Attempts are committed
before writing to the terminal and are separated by at least five minutes.
After three attempts, the request remains pending with delivery outcome
`needs_attention`; it does not expire or trigger an endless loop. Accepted
requests do not receive additional automatic reminders.

Inbox responses include `delivery.attempts`, `last_attempt_at`, `last_read_at`,
and `outcome` metadata when present. Viewing the owner's dashboard does not
count as the agent reading its inbox. The agent's own inbox read records its
read time. A successful terminal write means a reminder was submitted, not that
the agent accepted or completed the assignment.

New session instructions tell agents to check the inbox at the start of an idle
turn and before unrelated work. The service does not grant permission to execute
peer instructions and never answers approval prompts.

## Verification

Runtime tests cover persistence through restart and long delays, explicit
expiry, duplicate delivery, concurrent checks, newly arriving work during a
cooldown, cancellation during readiness checks, approval/draft/active-terminal
protection, retry budgets, and owner-view versus agent-read metadata.
