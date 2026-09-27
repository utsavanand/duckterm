# Pie in the sky

Status: vision and ideas, not commitments. Started 2026-09-25. Nothing here is scheduled
unless it also appears in [roadmap.md](roadmap.md).

## Vision: three levels

The goal is a multi-agent operating system that turns a person's ideas into
shipped work without tying them to a screen and keyboard.

1. **A validated multi-agent system for building.** Teams of agents that
   reliably ship code and do research, with roles, hand-offs and checks that
   work every time. This is where DuckTerm is today (see the AOS blueprint
   idea below).
2. **An ideation pipeline.** A defined process for how ideas enter the system,
   get shaped into specs, and are handed to the agent teams, and for where and
   how the person steps in while the agents work.
3. **Hands-free, two-way flow of thought.** Work continues while the person
   walks or is away from the desk. They get the context they need through voice
   or other non-screen channels, speak ideas and decisions back, and the system
   turns that stream of thought into finished work.

Each level depends on the one below it: ideation is only useful if the teams
executing it are reliable, and hands-free input is only useful if there is a
pipeline to turn it into work.

### Building blocks that already point at levels 2 and 3

- **Answering Oracle without typing** (roadmap F11).
- **Request status updates** (received / progress / done) produce short
  "what you need to know" messages that could be read aloud.
- **The product session** is a manual level 2: the user thinks out loud, and
  the session shapes it into specs and routes them to the architect.

### A possible first step toward level 3

A voice check-in that works away from the desk:

- **Listen:** a spoken digest of what changed since the last check-in: which
  sessions finished, which are waiting on the user, which requests are done.
  Built from the progress digests and inbox status the system already records.
- **Speak back:** the user answers approvals and decisions, or dictates a new
  idea. Ideas land in the ideation pipeline as a draft, not as direct
  instructions to agents.
- **Stay safe:** anything irreversible (merge, release, delete, spend money)
  still waits for a confirmation the user can review later on a screen.

Open: which device carries it (phone, earbuds, watch); whether it's pushed on
a schedule or pulled on demand; how a rambling voice note becomes a clean spec.

## Where DuckTerm stands

DuckTerm's closest competitor is Databricks Omnigent, not Conductor. DuckTerm
should compete on the experience of one developer running many agents, and
close the prompt-to-merge gap that Conductor users value.

## Competitive landscape

Sources: product pages, launch posts and one review video, not hands-on testing.

| Product | Agents | Where it runs | Shared live sessions | Notes |
| --- | --- | --- | --- | --- |
| [Databricks Omnigent](https://www.databricks.com/blog/introducing-omnigent-meta-harness-combine-control-and-share-your-agents) | Claude Code, Codex, Cursor, Pi, custom | Local, or cloud sandboxes (Modal, Daytona, Fly.io, Railway) | Yes, by URL | Apache 2.0, launched 2026-06-13. Governance rules, spending caps, OS sandboxing. Calls itself a "meta-harness". |
| Conductor | Claude Code, Codex | Local Mac app | No | Own interface around the CLIs, so new CLI features arrive late. Best PR and CI flow. |
| Superset | Any CLI | Local | No | Raw terminal, always current. Weaker PR flow, no CI status. |
| Cursor agent mode | Cursor's models | Local, plus cloud background agents | No | Reviewer found diffs mixed up between agents. |
| Claude Squad, Crystal, Vibe Kanban | Claude Code and others | Local | No | Open-source managers of parallel agents in worktrees. |
| Claude Code web, Codex cloud, GitHub Agent HQ | The vendor's own agent | Vendor cloud | No | Platform companies building their own control panels. |

Where DuckTerm is different: a real terminal plus a rendered Messages view in
the same session, sessions messaging each other through an inbox, Ask Oracle
across all sessions, context-pressure warnings, and approvals from the
dashboard.

## What users value

From one review comparing Conductor, Superset and Cursor (March 2026). The
reviewer chose Conductor for its prompt-to-merge flow.

1. **Polished interface vs. real terminal.** Conductor lags new CLI features
   (`/simplify`, `/batch`); Superset's raw terminal is always current. DuckTerm
   already offers both.
2. **Prompt to merge in one place.** One-click PR with a written commit message,
   CI status and logs in the app, one-click fix for failing checks, workspaces
   that move to done after merge. DuckTerm has none of these; installing
   meta-harnesses may cover part of it.
3. **A diff you can trust.** Cursor showed another agent's changes. DuckTerm's
   Diff tab is per session; it mixes changes only when two sessions share one
   checkout without separate worktrees.
4. **Review is basic everywhere.** No jump to definition, no comments across
   several lines, no whole-file view, so users still open an IDE.
5. **Small touches.** Per-worktree setup scripts (copying `.env`), run/stop with
   Cmd+R, plan with one model and implement with another, latest-turn diff,
   commit history view.

## Business

- **Solo developers:** crowded, many free tools, and the platform companies
  are building their own control panels. Weak as a paid product.
- **Companies:** the cross-vendor control panel pitch (audit trail, approval
  rules, spend per team, shared visibility) is now Omnigent's pitch, free and
  backed by Databricks.
- **Remaining opening:** the day-to-day experience of one developer running
  many agents, plus teams sharing sessions on machines in their own cloud.

Before any enterprise work, talk to 5–10 engineering managers.

## Idea: plan hand-off

Plan with one model, implement with another (e.g. Opus plans, Codex
implements). Design request sent to the architect.

- A **Hand off** action on a session: pick agent and model.
- Starts a new session in the same worktree, seeded with the plan, editable
  before sending.
- Shows as a child of the planner in the session tree.

Existing plumbing: `POST /sessions/:key/fork` and Claude conversation forks.
Open: same worktree or new fork; where the plan text comes from (last message,
Claude's plan-mode file, or user's choice); fit with meta-harnesses.

## Idea: DuckCloud

Run your ducks in the cloud. v1 supports GCP and AWS in the user's own
account. It is the product name for the `remote-session` branch, not a new
build. Design request with the architect.

- DuckTerm sets up the VM in the user's account; credentials and code stay there.
- One click from the New Session form, plus the existing "Move to remote".
- Sessions keep running after the laptop closes.
- Later: others join a session through the sharing relay
  ([session-sharing-design.md](session-sharing-design.md)).

Deferred: hosting machines ourselves (billing, customer isolation, security,
on-call) until developers without a cloud account ask for it.

Order: `remote-session` is merged (GCP only; per the architect, 2026-09-26)
→ easy "sign in and pick a size" setup, including AWS → collaboration via the
relay. Open: Omnigent reuses sandbox providers instead of provisioning VMs —
should DuckCloud?

## Idea: session folder view

A **Files** tab next to Terminal, Messages, History and Inbox, showing the
session's folder as a tree; clicking a file opens it. Wanted soon.

Half exists: `GET /file` reads and writes files in a session's folder, and
`web/src/FileEditModal.tsx` edits one, but you type the path. Missing: a
folder-listing route and the tree view.

- Tree starts at the session's working folder (worktree if any), loads one
  level at a time.
- Hides `.git`, `node_modules` and gitignored files, with a toggle.
- Marks files the session changed, linking to the Diff tab.
- Click opens a read-only viewer; an Edit button reuses `FileEditModal`.
- The listing route refuses paths outside the session's folder.

Open: view-only first or editable; live refresh or on demand; how large and
binary files show.

## Idea: modes and Entourage

One DuckTerm with modes set per folder or project, not separate products.
Separate builds would multiply releases, tests and docs.

| Mode | Panels that come first | "Done" means |
| --- | --- | --- |
| Code | Worktrees, branches, diff, PR and CI status, plan hand-off | PR merged |
| Research | Artifacts, sources, docs, notes, comments | Report published or shared |
| General | Messages, inbox, Oracle, connectors | Task closed |

Lead with Code; add Research after Code has the prompt-to-merge flow. General
is roughly DuckTerm today.

**Entourage** is a separate consumer productivity product that may reuse
DuckTerm's engine. Still to define: audience, what it does that General mode
doesn't, and what runs underneath.

## Idea: meta-harnesses and AOS blueprints

DuckTerm installs two kinds of things on top of agent CLIs: a meta-harness
changes how *one* agent works; an AOS blueprint sets up *a team* of agents
that work together.

| Term | What it is | Example |
| --- | --- | --- |
| Harness | An agent CLI DuckTerm drives | Claude Code, Codex, Copilot |
| Meta-harness | An installable collection of skills, hooks and practices for harnesses | uv-suite |
| AOS blueprint | An agentic operating system: a pre-configured team of agents with roles, instructions and skills to coordinate | Coding team, research team |

Meta-harness installs already exist in part (the Harnesses button,
[harnesses.md](harnesses.md)); the vocabulary is roadmap F5.

**AOS blueprint for coding** brings up, for example: product manager,
architect, dev 1, dev 2, frontend dev, QA, release engineer. Each gets:

- a role and instructions (who it takes work from, who it hands off to)
- the meta-harnesses and skills that role needs
- its place in a DuckTerm folder, so the inbox scopes it to its team
- the hand-off rules: requests, acknowledgements, progress and done messages

The user can change a blueprint after installing it: add or remove roles,
edit instructions, swap the agent or model behind a role. Other blueprints
cover other work, such as research (lead, searchers, fact-checker, writer).

**We are already running one by hand.** The DuckTerm folder's sessions
(product, architect, main-dev, ui-dev, main-qa, connectors-dev,
feature-remote-session) are a coding AOS set up manually. The first blueprint
could be this team, written down so it installs in one step.

Depends on: request status updates (received / progress / done), plan
hand-off, and the inbox. Open: the blueprint file format and how it relates
to `duckterm-harness.json`; whether a blueprint can require specific
harnesses per role; how a running team is upgraded when its blueprint
changes; how blueprints are shared or published.

## Open decisions

- [x] `remote-session` merged (per the architect, 2026-09-26); DuckCloud still needs setup flow, cost/idle controls and AWS.
- [ ] Review the architect's plan hand-off design and UI preview.
- [ ] Review the architect's DuckCloud design, including build vs. sandbox providers.
- [ ] Keep the term "meta-harness", which Omnigent also uses?
- [ ] Send the session folder view to the architect for design and preview.
- [ ] Send modes to the architect now, or after DuckCloud and plan hand-off?
- [ ] Define Entourage.
