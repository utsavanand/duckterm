# Diagnosing server stalls

The CLI server automatically records Python thread stacks if its event loop
cannot run its heartbeat for ten seconds. The log is `server-stalls.log` in
the instance's DuckTerm home (normally `~/.duckterm`). It includes the version,
process ID, function names, file paths and line numbers, without local variable
values, request bodies or transcript contents. It is local and mode `0600`.

The Python standard library's native faulthandler timer produces the report;
no profiler installation or administrator access is needed. Waiting for an
asynchronous operation does not count as a stall when the loop remains responsive.
Each continuous stall produces one report. The heartbeat rearms after recovery,
and shutdown cancels the timer before closing the log. Reports can also occur
around system suspension; a report alone does not establish the cause.

The log rotates between reports when it reaches 1 MiB, keeping one previous
file named `server-stalls.previous.log`. A single bounded faulthandler dump can
exceed that threshold. A log
failure disables diagnostics for that run and does not stop the server.

This is diagnostic coverage, not a fix for a blocking query. Use the captured
Python caller to reproduce and fix the underlying work. The timer starts in
the CLI's server loop; it does not cover construction before the loop starts,
direct in-process `Server.serve()` users, or stalls shorter than ten seconds.
