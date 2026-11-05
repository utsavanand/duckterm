# Diagnosing server stalls

Automatic faulthandler stack dumping is removed. In the November 3, 2026
incident, a dump failed to finish and the event loop waited indefinitely in
`cancel_dump_traceback_later()`. Diagnostic collection must not become a
dependency of server progress or shutdown.

Existing `server-stalls.log` and `server-stalls.previous.log` files in the
instance's DuckTerm home are retained as historical evidence. New servers do
not arm that timer or write these logs. Removing it prevents this diagnostic
wait; it does not fix the blocking work that originally triggered a dump,
or the separate slow-startup problem.

On macOS, capture a native sample from a separate terminal while the server
is unresponsive: `sample <server-pid> 3 -file /tmp/duckterm-server.sample.txt`.
Confirm the server PID first. Sampling does not require restarting the server
or stopping agent sessions. Native samples can identify blocked system calls
but may not identify the Python caller. Preserve the sample, timestamps,
server version and existing stall logs for investigation. A prior dump may
also reflect system suspension; it does not alone establish a slow code path.
