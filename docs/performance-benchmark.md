# Reproducible server performance measurements

Run `scripts/benchmark_server.py` with the checkout's Python to compare a fixed
workload before and after a change. It uses Python's standard library and tmux;
it does not require model access, connectors, credentials or a browser.

```sh
.venv/bin/python scripts/benchmark_server.py --output /tmp/before.json
# Run the identical command from the candidate checkout, changing only the output path.
.venv/bin/python scripts/benchmark_server.py --output /tmp/after.json
```

The default is 16 sessions, three clients, three repetitions of each workload,
and a six-second warmup in a fresh server for every trial. Select `--edition lite`,
`code` or `full` explicitly when comparing editions; the default is `full`.
Use the same machine and Python version, avoid overlapping gates or builds, and
compare all repetitions. A single fast run is not evidence of an improvement.
The output records the revision, working-tree status, platform and configuration.

## Workloads

- **Idle:** real fixture terminals remain idle while each client requests
  `/sessions` five times, sleeping two seconds after each request. This includes
  background terminal monitoring and dashboard-style polling, not a zero-client
  idle measurement. `--clients`, `--rounds` and `--interval` set this schedule.
- **Output:** each terminal emits 1,024 lines of 120 ASCII payload bytes. A real
  terminal WebSocket consumes the initial screen before triggering output, then
  verifies the exact payload count and completion marker. Measurement continues
  until the server's background output tails also consume the marker. Latency is
  input-request to receipt of the completion marker; total phase CPU also includes
  attachment and background drain. `--lines` and `--width` set the payload.
- **Reconnect:** each client opens `/stream`, reads its initial SSE event, then
  disconnects, using the same count and cadence as the idle workload. Before
  measurement, 100 synthetic notifications with 1,024-byte message fields seed
  the event history. `--replay-events` and `--event-bytes` set the fixture size.
  This measures event-stream initialization, not complete browser reconnection.

Use `--sessions 40` for a larger fleet or `--scenarios idle output` to select work.
A quick correctness run can use `--sessions 2 --clients 1 --rounds 1 --interval 0
--warmup 0.2 --lines 32 --repeat 1`; its short durations are not meaningful CPU
baselines. The default warmup allows adaptive terminal polling to settle.

## Reading the results

Each trial records server CPU seconds, percentage of one CPU core, elapsed time,
completed-child CPU, subprocess starts, tmux command counts, request/completion
latency percentiles, received bytes and session cleanup counts. Output trials also
record the independently checked payload byte count. Compare CPU per identical
workload as well as latency; a slower run can show a lower CPU percentage while
using more CPU seconds.

Server CPU excludes the driver, tmux server, fixture agents, pane writers, browser
and model processes. Completed-child CPU includes only reaped direct-child work;
it is not total descendant CPU. Spawn counts observe the server's subprocess
calls and command names, not every process on the machine. The tmux command
counter covers the shared helper; direct control-mode attachments are reflected
in subprocess starts instead. Instrumentation itself adds some overhead.

`server_lifetime_peak_rss_bytes` is the process high-water mark including startup,
not current resident memory, per-phase allocation or whole-app memory. HTTP p95
is the nearest-rank percentile of that trial's samples; defaults produce only
15 samples. Neither these percentiles nor run-to-run differences are statistical
confidence intervals.

These fixtures use a fresh small database and generic terminals. They do not
model large retained checkpoints, real harness permission screens, network
providers, rendering or every source of production load. The existing
`scripts/profile_terminal.cjs` separately measures synthetic browser rendering
and input latency. Keep browser and server results labeled separately.

## Isolation and cleanup

Every trial creates a unique `duckterm-bench-*` tmux socket and a temporary home.
Inherited DuckTerm targets and credentials are discarded. Fixture sessions carry
`test:true`; normal shutdown stops them, purges their records and checks that no
session rows remain. The driver kills only its newly generated tmux namespace and
removes its temporary directory, including after a workload failure. It verifies
that the namespace contains no surviving sessions. Model summarization, Oracle
and external terminal windows are disabled.

An error exits nonzero and does not publish a successful result. Like other local
process runners, a forcibly killed driver cannot guarantee cleanup. If that
happens, inspect the temporary `duckterm-benchmark-*` directory's `owner` file
before cleaning its exact socket; never sweep other sessions or shared sockets.

This is an opt-in benchmark, not a timing gate. The regular test suite checks its
accounting, isolation, output integrity and failure cleanup without requiring a
particular speed. Use the measurements to identify a reproducible cost before
changing architecture or introducing a native implementation.
