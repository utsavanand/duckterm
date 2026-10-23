# PR review platform — review an agent's pull request without leaving DuckTerm

Status: owner-requested 2026-10-22. Product spec with build plan, after two
rounds of owner review on the interactive mock. Nothing here is built.

Owner, 2026-10-22:

> "Right now to look at a PR in GitHub is really hard. We need to be able to
> use this PR platform… first the ability to see the diff from the branch the
> PR is based on… the other tab shows a different journey of the PR, cards…
> what is the core change, what files can be safely ignored, what files are
> high risk… a chat bar always remains relevant… the third pane shows the
> history of the repo itself."

Second round, same day: diff viewer approved as drawn; chat moves to a
collapsible right panel; cards go from five to ten; history draws
architecture per release only, never per merge; "ten cards is fine, write
the spec and build plan".

Mock: `PR review platform mock (debate draft v2)` in this session's artifacts.
Sample data in the mock is the real merged PR #307 (remote coding wizard).

## 1. What this is

A review workspace inside the dashboard for a pull request that one of the
folder's sessions produced. Three tabs and a chat panel:

| Part | Job |
| --- | --- |
| Diff | The change against the branch it will merge into, files ordered by risk, with viewed state that survives pushes |
| Review | Ten generated cards that explain the change, where the risk is, what is tested, how the author got there, and what remains open |
| History | One node per release tag with the architecture at that release; compare any two |
| Chat | A right panel, open by default, collapsible, scoped to this PR and the session that wrote it |

Two list views lead into it: a **PRs** tab on every session and a
**Pull requests** tab on every folder.

The problem it solves is specific to agent-written PRs: they arrive without
the author's reasoning, they run larger than human PRs, and the author is a
session in the next pane rather than a person on another time zone. Every card
can send its findings to that session's inbox.

Terminology follows `docs/terminology.md`: DuckTerm is a multi-agent
orchestrator, a harness is an agent CLI, the Oracle answers questions from
session data.

## 2. What already exists, and what this reuses

Verified against `origin/main` at c8963ef (2026-10-21):

- `src/duckterm/agent_merge_git.py` `snapshot()` already computes merge-base,
  commit count and a diffstat between two branches. Its `_git()` helper
  (stripped `GIT_*` env, `GIT_OPTIONAL_LOCKS=0`, 5 s timeout) is the pattern
  for every git call below.
- `GET /sessions/:key/diff` returns `git diff HEAD` only (uncommitted work).
  `web/src/SessionDetail.tsx` has a `DiffView` that nothing imports and whose
  empty state says the base-branch diff "is coming soon". Both are replaced.
- `src/duckterm/memory_provider.py` `generate(harness, model, prompt,
  schema=None)` runs an isolated model call (Claude: `--print --tools ""`,
  hooks disabled; Codex: `exec --ephemeral --sandbox read-only`) and returns
  JSON matching a schema. Every card uses it. No new LLM plumbing.
- `src/duckterm/connectors.py` already obtains a GitHub token
  (`gh auth token` or the connector); `github_projects.py` already calls
  `api.github.com` with urllib. PR status uses the same client. No `gh`
  subprocess for PR data, no new dependency.
- `web/src/render.ts` has marked + DOMPurify + highlight.js with the `diff`
  grammar registered; mermaid is lazy-loaded in `Messages.tsx`.
- Session timeline (`docs/session-timeline-api.md`), checkpoints and the
  inbox provide everything card 8 needs.
- Folder view already has a tablist (`FolderView.tsx`, `tab: "chat" |
  "artifacts"`); the session view has a view union in `App.tsx`. Both grow by
  one member.

No schema change in v1. Viewed state, card caches and architecture maps are
JSON files under `~/.duckterm/review/`, keyed by repository and head commit,
because they are caches that can be lost without harm. If a second consumer
appears (analytics, multi-Mac sync), move them to SQLite then.

## 3. Where it lives

**Session → PRs tab.** Seventh view after Memory. Lists every branch pushed
from this session's worktree, with its PR if one exists. Columns: number,
title, branch → base, status, checks, size (+/−), updated, Review button.

**Folder → Pull requests tab.** Third tab after Chat and Artifacts, same rows
across the folder's sessions, subfolders included, with a From column naming
the session. Filters: Needs you, Open, Merged, By session.

**Review workspace.** Opens in the middle pane, breadcrumb back to the list.
Header: title, branch → base, commits, files, +/−, CI, mergeable, Oracle
review state, authoring session, age. Actions: Chat toggle, Open on GitHub,
Request changes, Approve.

A PR is **claimed** by the session whose worktree branch it was pushed from
(`git -C <worktree> rev-parse --abbrev-ref HEAD` matched to the PR's head
ref). A PR raised by hand shows From: owner. A branch pushed with no PR shows
as *Pushed, no PR* with an Open PR… action that opens GitHub's compare page.

### Status model

| Status | Source | Needs you? |
| --- | --- | --- |
| Pushed, no PR | branch ahead of base, no PR with that head | no |
| Draft | GitHub `draft: true` | no |
| Open · awaiting review | GitHub open, no review from owner | yes, if the release rule says the owner reviews it |
| CI failing | GitHub check runs, any `conclusion: failure` | yes, unless draft |
| Conflict with base | local `git merge-tree --write-tree <base> <head>` exits non-zero | yes |
| Ready | open, mergeable, CI passing, approved | yes, for release PRs |
| Merged · vX.Y.Z | GitHub `merged_at`; version from the first tag containing the merge commit | no |
| Closed | GitHub closed, not merged | no |

Conflict is computed locally so it shows before GitHub's `mergeable` field
settles and works offline. Polling: GitHub every 60 s while a PR list or
workspace is visible, never otherwise; one request per repository using
`GET /repos/{owner}/{repo}/pulls?state=all&per_page=100`, then check runs for
open PRs only.

## 4. Diff tab

**Data.** `git diff --find-renames <merge-base>...<head>` on the session's
worktree (or on the folder's repository when the worktree is gone), parsed
server-side into files and hunks. Works before a PR exists. `-w` when Hide
whitespace is on (default on). File content for split view and "expand
context" comes from `git show <rev>:<path>`.

**File list.** Grouped into three buckets, risk first, then by path. Each row:
viewed checkbox, path, +/−. Headers show counts. A ⋯ menu moves a file to
another bucket for this review; the move is remembered with the viewed state.

**Toolbar.** Base and head commits, Unified / Split, Hide whitespace, Only
unviewed, *Since my last look · N files* (diff of `<last seen head>...<head>`,
restricted to files whose hunks changed).

**Viewed state.** Stored per file as the SHA-1 of its hunk text with whitespace
stripped. A push that does not change a file's hunks keeps it viewed; a push
that changes them clears it. This is the behaviour GitHub lacks and Reviewable
and Gerrit have.

**Viewer.** File header with path, hunk count, last commit touching it, Prev,
Next unviewed, Mark viewed. Below it the *why* box: the bucket and the
one-sentence reason from card 3, in the bucket's colour. Then the hunks,
rendered through the existing highlight.js diff grammar. Files over 2 000
changed lines render their first 400 with an Open full file control; binary
files show size only. Inline notes from the cards appear between hunks as
links back to their card. Every hunk header has Ask about this hunk, which
opens the chat panel with the hunk quoted.

### Risk buckets

Deterministic, computed server-side, no model call. A file starts in the
highest bucket any rule puts it in.

| Rule | Bucket |
| --- | --- |
| Path under `src/duckterm/persistence/`, or adds/changes a `CREATE TABLE`, `ALTER TABLE`, migration | High |
| Hunk adds or changes a line matching `Lock(`, `with self._lock`, `asyncio.Lock`, `threading.` | High |
| Hunk adds or changes an HTTP route registration or an auth decorator (`_require_owner`, `authHeaders`) | High |
| Path under `scripts/`, `mac/` (excluding `*.md`), or the installer | High |
| New file with more than 100 added lines, not a test | High |
| File changed in more than 10 commits in the last 30 days (hotspot) | High |
| Any other `.py`, `.ts`, `.tsx` under `src/` or `web/src/` | Read |
| Test files (`tests/**`, `*.test.*`, `*.spec.*`) | Read |
| `*.md`, `*.json` lockfiles, `package.json` with only a version change, images | Skim |
| Diff is whitespace-only, rename-only, or only string literals changed | Skim |

Card 3 shows which rules fired per file so the reviewer can disagree.

## 5. Review tab — the ten cards

A strip of ten tabs above a carousel. Each strip tab shows the card's title and
a one-line verdict, coloured by severity, so the strip alone says where the
findings are. Arrow keys and ‹ › move between cards. Every file reference in
a card is a link that opens that file in the Diff tab; "Send to <session>"
posts the card's findings to the authoring session's inbox through the
existing `session ask` path and records the request id on the card.

Cards are generated through `memory_provider.generate` with a JSON schema per
card, cached under `~/.duckterm/review/<repo>/<pr-or-branch>/<head-sha>/`, and
regenerated when the head moves. Inputs are assembled server-side; the model
never runs tools. Cards render as they arrive; a card whose inputs are missing
is omitted with a one-line reason, never guessed. Target: diff-only cards
within 60 s of opening a PR the first time; cached thereafter.

| # | Card | Inputs | Output (schema) | Deterministic part |
| --- | --- | --- | --- | --- |
| 1 | Intent vs diff | PR title and body, diff, file list | 2–3 paragraph summary; table of claims with `in_diff: true/false/partial` and evidence paths; "present but not claimed" list; diagram as mermaid of the changed modules | Claim extraction is the model's; evidence paths are checked to exist in the diff |
| 2 | Walkthrough | Diff, buckets, import graph of changed files | Ordered stops, each `path`, `hunk`, `title`, `why`, `bucket`; reading budget per bucket | Budget = lines ÷ 25 per minute for High, ÷ 60 Read, ÷ 200 Skim |
| 3 | Risk map | Buckets, rules fired, hotspot counts, revert history (`git log --grep=Revert -- <path>`) | One reason per High file; signals table | Entire bucket assignment (section 4); treemap from line counts |
| 4 | Blast radius | Symbol index of base (section 7), diff | Table: changed symbol, callers in diff, callers outside diff, tested; call graph as mermaid | Callers come from the index; the model only writes the one-paragraph consequence |
| 5 | Behaviour changes | Diff, test names and assertions, PR body | Rows: situation, before, after, who notices, `regression_risk` | — |
| 6 | Tests | Diff, test files, gate coverage report when present (`/tmp/duckterm-gate.log` or CI artefact) | Coverage table per risky change, gaps, up to 3 suggested tests with names and one-line bodies; slop findings | Coverage bars only from a real report; `toBeTruthy`/`toBeDefined`/no-assertion detection is a regex pass |
| 7 | Guardrails | `AGENTS.md`, `.claude/rules/*.md`, commit messages, diff | Table: rule, pass/finding/n-a, location | Commit format, trailer check, stdlib-only import check, retired terms are regex; slop rules go to the model with the rule text |
| 8 | How the author got here | Authoring session's timeline, checkpoints, inbox exchanges, hook-captured gate runs | Trajectory events with turn numbers; abandoned approaches; owner decisions quoted; verification done; context used | Event list is data; the model writes the four summaries |
| 9 | Release impact | Diff paths, low-risk rule from `AGENTS.md`, CI status, merge-tree result, version and README wheel link | Checklist with evidence; verdict `low_risk: true/false/conditional` with the condition | Schema/installer/security path checks are path rules; CI and conflict are facts |
| 10 | Open questions | Cards 1–9 | Items with `kind: unverified/confirm/cleanup/manual`, each with Ask or Send action | — |

Card 8 reads only the authoring session's data on this Mac; for a remote
session it uses the transferred checkpoint summary and says so.

## 6. Chat panel

Right panel, 360 px, full workspace height, open by default; the › in its
header collapses it to a slim Chat tab on the right edge; the Chat button in
the workspace header toggles it too. Below 1 000 px it stacks under the panes.

Context sent with every question: PR title and body, the diff, the base
commit, the module map for the base release, card results so far, and the
authoring session's latest checkpoint summary. Files from the base commit are
fetched on demand by path when the model asks for them (one round trip, owner
token). Answers cite `path:line`; the client turns citations into links that
open the Diff tab at that line. Four suggested questions sit under the input;
"Draft request-changes" produces a review body from card 10 with a
*Post to GitHub as review* button that requires a click and posts through
`POST /repos/{owner}/{repo}/pulls/{n}/reviews`.

Asking from a hunk quotes the hunk header and path. Transcript is kept per PR
in the same cache directory; it is not a session and never appears in the
sidebar.

## 7. History tab

One node per release tag (`git tag --sort=creatordate`, tags matching
`v*`). Node: date, version, one-line "what", count of merges. Click a node to
view its architecture; shift-click a second node, or *Compare with previous*,
to show the difference: added (green), changed (amber), removed (red, dashed), unchanged.
The release the open PR shipped in, or will ship in, is marked. A filter
limits nodes to releases that touched the PR's files.

**Module map.** One JSON per tag: `{modules: [{id, name, lane, files: [...]}],
edges: [[from, to]]}`, lanes Clients / Server / Runtime. Built by one
`memory_provider.generate` call over the import graph at that tag (Python
`ast` imports, TypeScript `import` statements), cached under
`~/.duckterm/review/<repo>/archmap/<tag>.json`. Changed = any file in the
module changed between the two tags. Maps for existing tags are generated
lazily the first time History opens, newest first, so the tab is usable
within seconds and fills in behind.

Merges per release: `git log <prev-tag>..<tag> --merges --format=%s`. A tag's
merges list links each PR number to its workspace if the PR is known.

Per-merge architecture is explicitly out: the owner judged it too noisy and
too expensive, and the mock confirmed one node per release reads well.

## 8. Symbol index (for card 4)

Per base commit, cached. Python through `ast`: top-level functions, classes,
methods, and the names each function body references. TypeScript and TSX
through a regex pass over `export function|const|class` and `import { … } from`.
Callers outside the diff are index entries referencing a changed symbol from a
file not in the diff. The limits are stated on the card: no dynamic dispatch,
no string-keyed routes, TypeScript is name-based.

## 9. API

All owner-token guarded, all JSON, same routing style as `/sessions/:key/*`.

| Method and path | Returns |
| --- | --- |
| `GET /sessions/:key/prs` | rows for the session's branches |
| `GET /folders/:path/prs` | rows across the folder, with `session` |
| `GET /review/:repo/:ref` | header: PR fields, status, base and head shas, counts |
| `GET /review/:repo/:ref/diff?w=1&since=<sha>` | files with bucket, rules fired, hunks, viewed hashes |
| `PUT /review/:repo/:ref/viewed` | `{path, hash, viewed, bucket_override?}` |
| `GET /review/:repo/:ref/cards` | cached cards, each `{id, status: ready/pending/omitted, reason?, data}` |
| `POST /review/:repo/:ref/cards/regenerate` | starts generation; progress through the existing SSE stream |
| `POST /review/:repo/:ref/ask` | `{question, hunk?}` → `{answer, citations}` |
| `POST /review/:repo/:ref/send` | `{card, items, session}` → inbox request id |
| `POST /review/:repo/:ref/github-review` | `{event: APPROVE/REQUEST_CHANGES, body}` |
| `GET /repos/:repo/releases` | tags with date, merges, archmap status |
| `GET /repos/:repo/archmap/:tag` | module map |

`:repo` is the repository root path, URL-encoded, as `/browse` already takes;
`:ref` is a PR number or a branch name.

## 10. Web

- `App.tsx`: view union gains `"prs"`, a tab button after Memory, a pane
  rendering `PrList` or `ReviewWorkspace`.
- `FolderView.tsx`: tab union gains `"prs"`; the arrow-key handler that is
  hardcoded for two tabs becomes index-based.
- New components under `web/src/review/`: `PrList`, `ReviewWorkspace`,
  `DiffPane`, `FileList`, `HunkView`, `CardsPane` (one component per card),
  `HistoryPane`, `ArchMap`, `ChatPanel`.
- `api.ts` gains the endpoints above on the `api` object; components never
  call fetch directly, as today.
- Delete `web/src/SessionDetail.tsx` and the `/sessions/:key/diff` handler
  once `DiffPane` lands.
- Styling follows `theme.css` tokens; risk colours are three new tokens
  (`--risk-high`, `--risk-mid`, `--risk-low`) with light and dark values.

## 11. Security and privacy

- GitHub token comes from the existing connector path; nothing is written to
  GitHub without a click on Approve, Request changes or Post as review.
- Model calls run isolated exactly as checkpoints do; no tools, no hooks.
- Card 8 reads the authoring session's transcript locally; it is never sent to
  GitHub and never leaves the Mac except inside a chat answer the owner asked
  for.
- The review cache under `~/.duckterm/review/` holds diffs and model output;
  it is excluded from backups and artifacts by default.

## 12. Build plan

Six PRs. Each ships something the owner can use on a real PR the same day;
acceptance is the owner's live use, not the gate. Owner's word is needed
before each visible UI ships, since each PR changes the dashboard.
Recommendation for who builds what is below; the owner routes.

| PR | Scope | Tests | Done when |
| --- | --- | --- | --- |
| 1 · Lists | Status model, GitHub client, merge-tree conflict, `/sessions/:key/prs`, `/folders/:path/prs`, both tabs, Review button opens GitHub for now | unit: status mapping, claim-by-branch, conflict detection with a fixture repo; e2e: both tabs render fixture rows | Owner sees their fleet's open PRs with correct status without opening GitHub |
| 2 · Diff | Base-branch diff, buckets and rules, viewed hashes, since-my-last-look, workspace shell with Diff tab, chat panel shell (toggle, empty), delete dead DiffView | unit: parser on 5 recorded diffs incl. rename and binary, every bucket rule, viewed-hash stability across a whitespace push; e2e: open PR, mark viewed, push fixture, viewed survives | Owner reviews one real PR's diff end to end in DuckTerm |
| 3 · Diff-only cards and chat | Cards 1, 2, 3, 5, 9, 10; strip and carousel; cache and regenerate; `ask` with citations; Send to session | unit: each schema validates on recorded model output, omitted-card path, citation→link parsing; e2e: cards render from fixture cache | Owner reads the cards for a real PR and sends one finding to the authoring session |
| 4 · Tests and guardrails | Cards 6 and 7; coverage report parsing; regex rule passes; rules text fed to the model | unit: slop regexes on fixtures with known counts, commit-format checker, stdlib import check; golden file per card | Card 7 catches a planted trailer and a planted `toBeTruthy` |
| 5 · Author and blast radius | Card 8 from timeline, checkpoints, inbox, hook-captured gate runs; symbol index; card 4 | unit: index on a fixture tree (Python and TS), outside-caller detection; card 8 event extraction on a recorded session | Card 8 shows a real session's reverted approach that the owner remembers happening |
| 6 · History | Releases, module maps, compare mode, merges per release | unit: import-graph extraction, map diff; e2e: click two nodes, the added module is marked | Owner compares two real releases and the marks match what shipped |

Suggested routing, for the owner to confirm: PRs 1, 2, 3 and 6 server work to
main-dev, web work to ui-dev; PR 4 and 5 to main-dev after PR 3; QA by main-qa
per PR; release-dev ships each under the normal rule (PR 1 is low-risk; PRs 2
onward add a UI the owner previews first). Each PR carries its docs; this spec
moves to `docs/done/` with PR 6.

Order rationale: PR 1 has value with zero model calls and proves the GitHub
path. PR 2 is the part the owner approved unchanged. PR 3 is the first model
work and reuses `memory_provider` as is. PRs 4 and 5 need new extractors and
are the most likely to slip, so they are isolated. PR 6 is independent of the
others and can run in parallel with 4 and 5.

## 13. Acceptance

- The owner reviews a real PR from one of their sessions, start to finish, in
  DuckTerm, and opens GitHub only to press Merge.
- Viewed state survives a push that changes other files.
- Every file reference in every card opens the right file; a wrong path is a
  bug in the card's evidence check, not a display issue.
- Cards state their omissions. No coverage bar appears without a report.
- A finding sent to a session arrives in that session's inbox with the hunk
  and the card name.
- History opens in under 3 s on the DuckTerm repository with maps for the
  newest two releases present; the rest fill in behind.

## 14. Out of scope for v1

Inline comments on GitHub lines (the chat drafts a review body instead),
repositories hosted outside GitHub, stacked PRs, merging from DuckTerm,
per-merge architecture, multi-repository folders, review of PRs whose branch
no session on this Mac owns beyond read-only, and a light-theme pass on the
treemap beyond token colours.

## 15. Open for the owner

1. Should Approve from DuckTerm count as the Oracle approval the release rule
   requires, or stay a plain GitHub approval?
2. Does the chat panel also belong on the session and folder PR lists, scoped
   to "which of these PRs…", or only in the workspace?
3. Default polling at 60 s while visible: acceptable against the GitHub rate
   limit for a fleet of 12 sessions in one repository (one list call plus one
   check-runs call per open PR, so about 15 requests a minute)?
