# Terminal reattachment without duplicate output

Reopening a terminal view or returning to an evicted view now gets its snapshot
and subsequent bytes from one tmux control-mode connection. Previously the
snapshot came from tmux while live output came from a file downstream of tmux's
pipe. Bytes already visible in the snapshot could arrive from that file later
and appear twice, including in an unfinished prompt.

Each attached view uses an output-only, ignore-size client on the existing instance
socket. One command list attaches, reads pane/cursor metadata, captures up to
2,000 history rows plus the screen, and captures any pending escape sequence.
The reader discards output before those replies and then delivers the ordered
post-capture output. It preserves UTF-8, octal-escaped raw bytes and capture
backslash escapes (which differ from live output); unknown escapes stay literal.
It filters to
the captured pane, and restores the cursor row and column. Screen row counts
keep protocol-looking text inside the capture from ending a reply early.

A control connection belongs only to its viewer. Closing, pausing or losing it
does not stop the agent, affect another viewer, or change the pane size. The
existing browser reconnect path obtains a fresh snapshot. `pause-after=5`
protects a stalled viewer from an indefinitely growing tmux backlog; receiving
`%pause` closes the feed so it cannot stay silently frozen. The snapshot is
limited to 8 MiB and each protocol line to 256 KiB. An independent reader drains
the control pipe into at most 32 queued chunks; overflow closes that client
without waiting for the browser to consume more data. Cleanup drains the
subprocess pipes while terminating it, with a kill fallback. The app's existing three-view
cache limits idle view connections per dashboard.

The private control channel sends only the initial capture commands. It does not
set tmux’s `read-only` flag: tmux 3.7 can select that client for a separate
`send-keys` command and reject normal terminal input. Input tests run with
viewers attached to guard against this version-specific regression.

The file spool and tail remain in place for state detection, logs and summaries.
Their delayed bytes never enter tmux-backed terminal viewers. Direct PTY-backed
sessions retain their existing bounded replay path. This is a backend transport
fix; panel fitting and Messages table layout remain separate UI work.

The control flags require tmux 3.2 or later. Acceptance was exercised against
system tmux 3.6b and bundled tmux 3.7c. Both synchronous commands and
control streams must use the same `tmux.client_command` binary/socket selection
when integrating this change with bundled tmux.

Tests reproduce delayed-file duplication on the old backend, check byte decoding
and protocol-looking text, attach multiple viewers during 400 numbered records,
and verify no missing or repeated records. Client disconnect/pause tests check
that the other viewer continues, a new view resnapshots, the agent PID stays the
same, an empty pane stays empty, and all temporary clients and sessions close.

All synchronous tmux operations reached from server coroutines run in worker
threads, including liveness property reads, captures, resize and lifecycle
commands. Database/state work stays on the event loop. Slow liveness replies
are awaited, not converted to False; delayed responses cannot masquerade as
agent exit. Approval callbacks use the ordered input queue.

Regression coverage includes a tmux response that depends on loop progress,
rich TUI captures and live output (OSC8, truecolour, emoji/CJK, invalid UTF-8),
and a stalled consumer whose client must close while its agent survives.
