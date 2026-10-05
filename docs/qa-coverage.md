# QA coverage and execution policy

Test counts measure executions, not feature coverage. Add a case when it detects
a different failure mode; rerunning an unchanged passing gate is not new evidence.

| Workflow / risk | Automated evidence | Remaining boundary |
| --- | --- | --- |
| Server replacement → adopt Claude → change model → same conversation | Two Python server processes, real SQLite, tmux pane, transcript parsing, resume/model argv | Synthetic CLI prompt and hooks; no live provider inference |
| Browser workflows | CI discovers all Playwright files; real local server and built dashboard | Some specs intercept API responses or mock the native bridge; their titles/assertions describe UI contracts, not backend acceptance |
| Browser fixture state | Runner tests success, failure and SIGTERM; private HOME, state file, loopback port and tmux namespace | Forced SIGKILL cannot execute cleanup; ephemeral CI hosts provide the final containment |
| Native transport, launch, report and clipboard data | macOS CI runs Swift package and focused native scripts | Does not establish packaged-app, Finder launch, signing, update or remote-host acceptance |
| Native report form | Repaired script compiles application dependencies; explicit local QA | Requires GUI session; separate from headless contract checks |

Run browser checks with `cd web && npm run e2e -- <optional spec>`. The wrapper
must start before Playwright so worker fixtures and the server inherit the same
private HOME. The browser cache is retained outside that disposable HOME.
Direct Playwright execution fails setup without the isolation environment.
Tests use synthetic agents and test-marked records; teardown removes their
private state and tmux namespace. The allocated port is released before the
server binds; readiness verifies the owned server and fails if that bind races.

Run `scripts/gate.sh` bare. It prints its unique log path; GATE_LOG can override
it. Dependency preflight fails before expensive tests. `--fast` deliberately
omits browser acceptance and must never be called a full gate. CI installs tmux
explicitly so real-pane tests do not silently skip on a minimal runner.

For a change, first select its regression and nearest real boundary. Run a full
gate once the candidate is ready; repeat only for code changes, a failed check or
an unresolved integration risk. Preserve negative identity, cancellation,
concurrency and cleanup tests even when happy paths overlap. Before deleting a
test as redundant, identify the identical contract covered elsewhere and show
that its failure sensitivity remains; no broad count-driven deletion is useful.

Release acceptance must separately record committed revision, package build,
installed app launch, dashboard health and any feature-specific native/remote
workflow. Keep synthetic-runtime validation distinct from supported real-CLI
contract checks and provider integration. Neither source-level green checks nor
a mocked WK message handler establishes a shipped Mac workflow. The packaged
release smoke remains a release responsibility, not a claim made by this CI job.
