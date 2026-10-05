# Session shell backend

The owner's shell is a separate `rd_<session-key>-sh` tmux session on the owning
host. It opens only on request, using the session worktree (or cwd) and the
owner's configured login shell. No database identity or schema migration is added.

All routes require the owner credential, including GET. Session API bearer
credentials are rejected. Browser cross-origin protection applies as usual.

| Route | Behavior |
| --- | --- |
| GET `/sessions/:key/shell` | Read status without spawning |
| POST `/sessions/:key/shell` | Open or return the existing shell; empty JSON object only |
| DELETE `/sessions/:key/shell` | Close, or request confirmation for a foreground job |
| GET `/sessions/:key/shell/terminal` | WebSocket attached to an already-open shell |

An open status includes `open`, `pane_id`, `foreground`, `confirmation_required`
and `confirmation_token`. A closed status has `open:false`, `foreground:null`
and `confirmation_required:false`. An unconfirmed busy close returns HTTP 409
with the current status and `closed:false`. After owner confirmation, retry with
`{"force":true,"confirmation_token":"<returned token>"}`. A token for a changed
pane, foreground process group or command requires confirmation again. This is
an observation check, not an atomic process freeze: a job may change immediately
after inspection. Unknown foreground state also requires confirmation. Background
jobs are not separately enumerated. Successful close returns `closed:true`.

The WebSocket uses binary input/output and text `{"resize":{"cols":93,"rows":27}}`
frames, like the agent terminal. Native clients send `X-Duckterm-Token`. Browser
clients offer `duckterm-shell` and `duckterm-owner.<owner-token>` as subprotocols;
the server selects `duckterm-shell`. Never put credentials in the URL. Input frames
are limited to 64 KiB, dimensions to 500 columns and 300 rows.

Collapse or disconnect closes only the viewer. Agent Stop/Restart leaves the shell
alive. Committed archive and delete destroy it, including after server restart.
Archive Undo leaves it alone. Reopening after a server restart finds it by derived
name and verifies its owner-shell tag; an unrelated terminal at that name is not
adopted or killed. An in-flight spawn settles before archive/delete can proceed.

The shell has no agent supervisor, capture pipe, lifecycle events or session
credentials. A helper clears inherited DuckTerm/agent identity variables *inside*
tmux, after global environment inheritance, then sets `DUCKTERM_INTERNAL=1` so
installed hooks remain inert. This is identity separation, not an OS sandbox:
commands run as the owner, who can intentionally access their own files. DuckTerm
does not record shell output in v1; tmux scrollback and the owner's ordinary shell
history still exist. Shells are excluded from agent reconciliation by their explicit ownership tag, not by a name suffix. Older agent keys ending in `-sh` remain discoverable and can still be archived or deleted, although they cannot open a sibling shell. Agent tmux
reads, writes, kills and liveness checks use exact targets to prevent prefix
matching a surviving sibling shell after the agent exits.

Only recorded DuckTerm-owned terminals qualify; watched/heartbeat sessions and
archived/merged/pending-archive sessions return 409. Missing host-local sessions
return 404; unavailable tmux returns 503. A missing project folder returns 409.
The request cannot override the host, cwd, environment or command. Remote clients
must route these endpoints to the selected session's owning host. The native/UI
transport integration is separate work: it must report an unreachable remote as
unavailable and must never retry on the local host.
