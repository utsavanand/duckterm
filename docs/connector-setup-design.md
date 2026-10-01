# Connector setup: design

Status: owner-approved 2026-09-28, partly implemented. Supersedes the
setup-guidance parts of [shared-connectors.md](shared-connectors.md);
credential storage, the launch-time shim, and the shared host are unchanged.

Owner decision (2026-09-28): **per-machine credentials.** Each machine holds
only its own, reusing the provider's own CLI login where one exists rather
than copying secrets between machines. Remote machines sign in with device or
pairing codes. Revocation is per machine. Recorded but deliberately not built:
if team sharing arrives, configure-once should come from a secrets manager
issuing short-lived credentials with an audit log, not a host holding
long-lived tokens.

## B2 is legibility, not plumbing

A trace run before implementation changed what this work is. Speaking MCP to
each enabled connector through the exact command the harness configs run
(`duckterm connector-run NAME`; initialize, then `tools/list`) returned GitHub
45 tools, Railway 34, Porkbun 25, with both harnesses carrying all three
entries. Nothing was broken. The reported symptom — "I have multiple
connectors but I don't know if I can really use them" — described the panel,
which could only ever say `enabled=True, detail=None`. That asserts a config
entry exists; it never shows that 45 GitHub tools reached an agent.

So proof of life ships first, ahead of the setup wizard below: a verify action
running the real harness path, the tool count it measured, and last-used drawn
from existing hook events. The wizard still matters for the not-yet-configured
case (Gmail here, every connector on a fresh machine) — it is simply not what
B2 asked for.

Bounded claim: verification proves the server starts and serves tools. It does
not prove an already-running session picked up a registration added after it
started; per-session Restart is the honest remedy for that.

## The problem

Connecting a provider works only when its prerequisites already happen to be
installed and logged in. When they are not, the dashboard dead-ends three
different ways: a disabled button with grey explanatory text (Railway, Hugging
Face), a failing toast after Connect (GitHub without `github-mcp-server`,
Porkbun without `uvx`), or a hand-written `<details>` block telling the user to
run a command in some other terminal (Gmail, Google Cloud).

Underneath that, one word is doing two jobs. `enable()` resolves a credential —
machine-wide, duckterm's own — and `_install()` writes an MCP entry into both
harness configs:

```python
def _install(name, *, home=None):
    command, args = _duckterm_bin(), ["connector-run", name]
    mcp_install.claude_install(name, command, args, home=home)
    mcp_install.codex_install(name, command, args, home=home)
```

Both harnesses, unconditionally, with no say. So "connected" means a credential
*and* two registrations, and the user cannot have one without the others. We
write `~/.codex/config.toml` on machines that have never run Codex and then
report `codex: ✓`.

## The tally

Which connectors MCP can serve, checked against what the code actually runs:

| Connector | MCP server | CLI exists | Verdict |
| --- | --- | --- | --- |
| GitHub | `github-mcp-server` (or Docker image) | `gh` | **MCP**, needs a binary |
| Porkbun | `uvx porkbun-mcp` | none | **MCP only** |
| Hugging Face | `mcp-remote` → hosted HTTP endpoint | none for search | **MCP only** |
| Gmail | `@artymclabin/gmail-mcp` | none | **MCP only** |
| Google Cloud | `@google-cloud/gcloud-mcp` | `gcloud` | **MCP**, wraps the CLI |
| Railway | `railway mcp` (bundled in the CLI) | `railway` | **MCP**, bundled |

**MCP covers all six.** There is no connector where the CLI is the only path,
so the tab recommends MCP everywhere and never recommends "just use the CLI".
That settles the question from the previous round: no CLI-only tier.

What the CLI is still for is narrower and worth naming exactly, because it is
not a fallback — it is a **credential source** and sometimes the **runtime**:

- **GitHub** — `gh auth token` supplies the credential (the alternative is a
  pasted PAT). The MCP server is a separate binary either way.
- **Google Cloud** — `gcloud auth login` supplies the credential *and* the MCP
  server shells out to `gcloud`, so the CLI is a hard requirement.
- **Railway** — `railway login` supplies the credential *and* the CLI contains
  the MCP server (`railway mcp`), so the CLI is a hard requirement.
- **Porkbun, Hugging Face, Gmail** — no CLI involved at any point.

So the CLI never appears in the UI as a choice. It appears as a setup step for
the three connectors that need it, which is what the steps mechanism below is
for.

## The split

Two levels, because they have genuinely different scopes:

| | Scope | What it is |
| --- | --- | --- |
| **Connection** | per machine | The credential. Resolved by `duckterm connector-run NAME` at launch, never written into harness configs. |
| **Availability** | per harness | An MCP entry in `~/.claude.json` / `~/.codex/config.toml`. |

The split already exists in the code — `enable()` versus `_install()`. It is
just not exposed. Exposing it fixes three things: we stop registering Codex on
machines without Codex, we stop paying tool-definition context in sessions that
need none of it, and "connected" starts meaning something checkable.

## Design

### 1. `status()` returns steps instead of a sentence

`status()` already runs every prerequisite check; it flattens the result into
one `detail` string the UI can only print. Return the checks:

```python
"steps": [
    {"id": "cli",   "title": "Install the Railway CLI", "done": True},
    {"id": "login", "title": "Sign in to Railway", "done": False,
     "run": "railway login --browserless",
     "help": "https://docs.railway.com/cli/login"},
]
```

Three kinds, distinguished by which field is present: `run` (a command),
`input` (paste a key), `help` only (a link the user must follow — creating the
Gmail OAuth client in Cloud Console, which no wizard can automate). `ready`
becomes `all(step["done"] for step in steps)` — the same value it holds today,
now carrying its reason.

### 2. Run steps open a session, and the wizard watches it

A Run step is a command whose output the user must see: pairing codes, install
progress, a consent URL. Duckterm already launches sessions and knows when a
command exits. Click Run, read the code, finish in a browser; on exit, status
re-polls and the checkmark flips.

Every provider that needs an interactive login has a pairing-code path —
`railway login --browserless` prints a code and falls back to it automatically
with no display, `gcloud auth login --no-launch-browser` prints a URL and takes
a verification code back, and GitHub's device flow is what `gh auth login`
already uses. This matters for more than convenience: sessions will run on a
remote VM (roadmap item 3), and a pairing code travels through the user's eyes
rather than a loopback callback, so the same flow works on both machines with
one implementation. A browser-popup OAuth design would need a second mechanism
for the VM.

### 3. Availability checkboxes (built)

Per harness, defaulted on. `_install()` takes the harness list instead of
assuming both, and deselecting one withdraws its existing entry — leaving it
behind would keep a harness serving a connector the panel no longer shows.
The choice is stored per connector, so it survives disable/enable; connectors
that predate the setting keep both, which is what they already had.

One change from the sketch above: a missing harness is **labelled, not
disabled**. A registration written for an absent Codex is not wrong — it
applies the moment Codex is installed — so the checkbox stays usable and the
row reads "Codex (not installed here)". What must not happen is the panel
reporting that a machine without Codex is serving tools to Codex, so
`status()` returns `harnesses_present` and the row names the agents it
actually reaches instead of showing a bare checkmark per config entry.

`enabled` now means every *chosen* harness carries an entry. Requiring all of
them would read a deliberately single-harness connector as disabled.

```
┌─ GitHub ───────────────────── Connected as @utsavanand ─┐
│  Credential: GitHub CLI login              [ Change ]   │
│  ✓ github-mcp-server installed                          │
│                                                         │
│  Available to:  [x] Claude Code   [x] Codex             │
└─────────────────────────────────────────────────────────┘

┌─ Porkbun ──────────────────────── Not connected ────────┐
│  ○ Install uv                              [ Run ]      │
│  ○ API key + secret                        [ Enter ]    │
│                                                         │
│  Available to:  [x] Claude Code   [ ] Codex             │
└─────────────────────────────────────────────────────────┘
```

## Where the pieces run

The wizard always acts on the machine whose `status()` it is showing. The
dashboard is a browser page; the credential and the MCP server live on the
session host.

```
         browser (any device)
                │
                │  1. dashboard shows the step and the pairing code
                │  4. user enters the code at the provider
                ▼
   ┌────────────────────────┐        ┌────────────────────────┐
   │   This Mac             │        │   Remote workspace VM  │
   │                        │        │                        │
   │  duckterm server       │        │  duckterm server       │
   │   ├ status() → steps   │        │   ├ status() → steps   │
   │   ├ 2. Run → session ──┼─┐      │   ├ 2. Run → session ──┼─┐
   │   │    railway login   │ │      │   │    railway login   │ │
   │   │    --browserless   │ │      │   │    --browserless   │ │
   │   │  3. prints code ───┼─┘      │   │  3. prints code ───┼─┘
   │   └ 5. exit → re-poll  │        │   └ 5. exit → re-poll  │
   │                        │        │                        │
   │  credential: Keychain  │        │  credential: 0600 file │
   │  MCP entries: the      │        │  MCP entries: the      │
   │  harnesses on this Mac │        │  harnesses on the VM   │
   └────────────────────────┘        └────────────────────────┘
        identical flow, because the code travels through the
        user's eyes, not through a loopback callback
```

Two consequences to state plainly:

- **Connection is per machine.** Connecting GitHub on the Mac does not connect
  it on the VM; the panel header already says "this computer". A migrated
  session gets the destination machine's connectors. The pairing-code flow is
  what makes a second pass cheap.
- **The shared connector host remains the set-up-once answer.** When a
  workspace is enrolled (`connector-attach`), credentials live on the host,
  `status()` reports `managed`, and the wizard shows no steps — there is
  nothing to install or sign into. Per-machine setup is the standalone path;
  the broker is the fleet path. Both already exist.

## What this is not

- No OAuth broker, no registered Duckterm OAuth app, no loopback callback
  server. Each provider's CLI already performs the pairing-code exchange and
  owns token refresh; re-implementing that means holding refresh tokens we
  currently never see.
- No CLI-only connector tier — the tally shows MCP covers all six.
- No connector directory, no deeplinks, no plugin contract, no generic step
  engine. Six connectors with one to three checks each.

## Order of work

1. **Availability per harness.** `_install()` takes a harness list; the API
   carries it; checkboxes in the panel; detect missing harnesses. Smallest
   change, and it makes `installed` honest.
2. **Steps in `status()`** plus the panel rendering them, Run steps as copyable
   commands. Replaces the two hardcoded `<details>` blocks and removes every
   dead end but the final click.
3. **Run-in-session** with exit-triggered re-polling. Turns copy-paste into one
   click and is what makes the VM case work like the Mac.

Each lands on its own. Proof of life (verify action, measured tool count,
last-used) ships ahead of all three, because it is what B2 actually reported.

## Notes

- `remote-session` has merged into `main`, so this work targets `main`. That
  branch's `status()` (credential source selection, identity verification,
  forget/revoke, Porkbun write scoping) is the baseline; `steps` joins its
  existing `sources`.
- The verify probe must hold stdin open until the reply arrives. Closing it
  early makes the GitHub server exit with "server is closing: EOF" and report
  zero tools — a working connector that looks broken, which is the exact bug
  class this feature exists to prevent.
- Usage is hook-derived. A connector used by a harness without hooks reads
  "no recorded use", never "unused".
- Gmail keeps a Link step no wizard can remove: Google requires each user of an
  unverified app to create their own OAuth client. The wizard checks the result
  and names what is outstanding.
- Google Drive has been raised as a want and does not exist yet. It would be a
  third Google connector with its own OAuth scope — a separate piece of work,
  not a toggle on the Gmail one.
