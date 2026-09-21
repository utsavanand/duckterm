# DuckTerm security review — September 20, 2026

Scope: this repository's current development checkout, including the in-progress
session API. Railway and other projects are excluded. Findings below are fixed
in the security/CI branch; this review does not imply that installed releases contain the fixes.

## Findings and changes

| Priority | Finding and prerequisite | Fix |
| --- | --- | --- |
| High | Terminal command construction only quoted strings containing spaces or single quotes. Shell syntax in a path, title, argument, or environment value could execute when a terminal command was launched. Harmless regression payloads demonstrated expansion and command separation. | Use `shlex.quote` for terminal arguments and tmux output-file paths. Reject trailing newlines and dot-directory session identifiers. |
| High | The HTTP gate accepted arbitrary Host values. A request with a foreign Host and no Origin could retrieve the token-bearing dashboard, leaving a DNS-rebinding boundary open. Origin checks also accepted unrelated localhost ports, including WebSocket connections that can expose events or accept terminal input. | Require a loopback Host and matching HTTP origin, including port. Reject non-loopback bind addresses. Regression tests simulate the hostile HTTP headers; no live DNS-rebinding attack was conducted. |
| Medium | Dashboard containment used a string prefix. A traversal into a sibling named `dist-private`, for example, passed the `dist` prefix test and disclosed its file. | Check resolved path ancestry with `Path.is_relative_to`. |
| Medium | AGENTS.md read/write routes checked the directory, then followed the file's symlink outside the allowed roots. The suggestion route did not apply the same confinement. Exploitation of writes/suggestions requires the owner token; OS file permissions still apply. | Resolve and check the actual AGENTS.md target for reads, writes, and suggestions. Tests verify that an outside target is neither disclosed nor overwritten. |
| Medium | Most HTTP routes read unbounded Content-Length bodies before dispatch; headers had no total-size cap or read deadline. WebSocket readers accepted arbitrarily large declared frames. | Limit HTTP headers to 32 KiB, general bodies to 8 MiB, session-API bodies to their existing 2 MiB, and inbound WebSocket frames to 1 MiB. Apply a 10-second HTTP request-read deadline. Reject duplicate headers, invalid lengths, and unsupported transfer encodings. These are per-request limits, not global connection quotas. |
| Defense in depth | An empty token file could make a missing token compare equal. Secret files were written before applying private permissions. The dashboard could also be framed or cached. | Regenerate empty tokens, reject empty expected tokens, and publish new secret files atomically with mode 0600. Send no-store and anti-framing headers on the dashboard. |
| Dependency advisories | The original npm audit reported 13 affected package entries: 10 high and 3 moderate. Counts include transitive parents; they are not 13 independently demonstrated application exploits. | Apply compatible lockfile updates, use patched Vitest 4.1.11, and override Mermaid's pinned `lodash-es` with 4.18.1. Retain Mermaid 12. Final `npm audit` reports zero vulnerabilities. |

The browser origin comparison follows the protocol/host/port boundary described
by [MDN](https://developer.mozilla.org/en-US/docs/Web/Security/Defenses/Same-origin_policy).
Shell escaping uses Python's
[`shlex.quote`](https://docs.python.org/3.12/library/shlex.html#shlex.quote).
The dependency changes address the
[Lodash code-injection advisory](https://github.com/advisories/GHSA-r5fr-rjxr-66jc)
and [Vitest file-read advisory](https://github.com/advisories/GHSA-82fw-gwwq-j7x9),
along with the compatible transitive updates reported by npm.

CI now explicitly grants its GitHub token read-only repository access and runs
`npm audit --audit-level=high`. Python security regressions run in the existing
pytest job. The browser regression runs through the existing Playwright command.

## Verification

- The initial security reproduction suite failed 13 cases before the fixes.
- Full Python suite: 437 passed; the subsequently added dot-directory validator
  cases were checked with the focused security tests.
- Frontend: 52 unit tests passed with Vitest 4.1.11; lint and type checking passed.
- Browser: 5 Playwright tests passed, covering session creation, terminal input,
  switching sessions, Shift+Enter, sanitized message HTML, and two Mermaid diagrams.
  The browser setup also rebuilt the production dashboard successfully.
- Ruff, Black, mypy, and the repository's documentation/test quality check passed.
- Both `npm ci --ignore-scripts` and standard `npm ci` installed the updated lockfile;
  final npm audit: zero
  known vulnerabilities. npm 10's upgrade resolver crashed, so npm 11 generated
  the lockfile in a temporary directory. The normal local npm 10 `ci` then passed.
- A credential-pattern scan found no matches in current tracked files. This was
  a limited scan for common token/private-key formats, not a full history audit.

## Trust boundary and remaining review limits

DuckTerm is a local owner application that deliberately executes commands and
reads project/configuration files with the user's OS permissions. It is not a
sandbox for hostile agents running as that same user. In particular, a local
process can read the owner token or fetch the local dashboard; session bearer
tokens scope cooperating API clients, not arbitrary same-user processes.
Host/origin checks protect the browser boundary, not this OS-user boundary.

No new cross-session authorization bypass was established in the reviewed
session-API paths: peer and question operations check the granted shared root,
participant identity, and session availability. Existing tests cover these
checks. This is not a proof that every API path is free of authorization defects.

Resolved-path checks do not eliminate races against another process changing
filesystem links between validation and I/O. Protection against hostile local
processes would require a stronger OS isolation model and descriptor-based file
access. Aggregate connection/resource quotas and deeper native macOS WebView
review remain outside this pass. No installed application was rebuilt or released.

Compatibility: custom ports on loopback remain supported. Non-loopback binds,
foreign Host aliases, cross-port browser origins, and dashboard embedding are now
rejected. The browser tests verify the supported same-origin dashboard flow.

## CI/CD follow-up

The isolated security/CI branch originally passed 444 Python tests, 48 frontend
unit tests, and 5 browser tests. After the session API landed on main, its changes
were integrated while preserving the 2 MiB session request limit; the combined
Python suite passes 470 tests. GitHub CI now
includes a dedicated Chromium job for the browser regressions. The release
script requires a clean main checkout and successful main push CI for the exact
commit, checking both before building and immediately before tagging.
Branch protection is a separate GitHub repository setting; it is not enabled
merely by merging the workflow file.
