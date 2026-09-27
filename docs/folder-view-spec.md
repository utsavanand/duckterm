# Folder view and Feature tracker — spec

Status: proposed by product for the owner, 2026-09-27. Not designed or built.
Owner's direction: "an important feature to build; spec it out properly."

> **2026-09-27 update:** F12 work tracking was reverted in v0.4.74 at the
> owner's request (too heavy). Sections 3 and 4 below assume F12's work items
> exist; they no longer do. Before design, the owner chooses: a Feature
> tracker with its own minimal card data (title, description, folder,
> column, assignee, evidence link), or waiting for the F12 redesign.

## Summary

Clicking a folder opens a **folder view**: a page about that folder and its
agents, acting as a mini Oracle scoped to the folder. Folders hold **widgets**.
The first optional widget is the **Feature tracker**, a Kanban board of coding
work assigned to the folder and carried out by its agents.

The Feature tracker is a board over the **work items that already exist**
(F12, shipped in v0.4.72 and reverted in v0.4.74; design in [collaboration-reliability-design.md](collaboration-reliability-design.md)), not a new ticket
system. That keeps it within the collaboration design's "don't build a
ticketing system" rule: one work table, shown per folder.

## 1. Folder view

**Opening it:** clicking a folder's name in the sidebar opens its folder view
in the middle pane. The chevron still expands and collapses the folder.
Today clicking the name toggles the folder, so this changes existing behavior.

**Contents (the Overview, always present):**

| Area | Shows |
| --- | --- |
| Header | Folder name, path, and Add widget, Grid view and Broadcast buttons |
| Counts | Sessions in total, and how many are busy, waiting on you, idle and done |
| Agents | Each session: name, agent and model, state, current activity, context used, its open work items |
| Needs you | Sessions waiting on an approval or answer, and blocked work items with their blockers |
| Ask this folder | A mini Oracle. Questions are answered from this folder's sessions only, e.g. "what's stuck here?" |

Subfolders' sessions are included, as the Grid view does today. Each agent row
opens that session.

## 2. Widgets

A folder has a list of enabled widgets, saved on the server so it survives
restarts. v1 has two:

| Widget | Default | Notes |
| --- | --- | --- |
| Overview | Always on | Section 1 |
| Feature tracker | Off; added with Add widget | Section 3. Meant for coding folders |

No widget plugin system in v1: the list is two fixed ids. Candidates for later
widgets, each only when wanted: Focus grid for the folder, folder artifacts,
spend for the folder, and research sources for Research mode
([pie-in-the-sky.md](pie-in-the-sky.md)).

## 3. Feature tracker (coding Kanban)

**Columns and how they map to work states:**

| Column | Work states | Notes |
| --- | --- | --- |
| To do | `proposed`, `accepted` | Unassigned cards are marked |
| In progress | `in_progress`, `blocked` | Blocked cards show their blocker in red |
| In QA | `in_qa` (new) | A PR exists and is with main-qa or release-dev |
| Resolved | `done` | Shows evidence: PR, commit or version. Last 14 days, then collapsed |

`dropped` items are hidden, with a toggle to show them. The only schema change
is the new `in_qa` state.

**A card shows:** title, assigned session, state age (e.g. "in progress 3h"),
blocker if any, evidence link if any, and staleness from the existing one-hour
rule.

**Adding a card:** "New feature" takes a title, a description and optional
acceptance criteria, and creates a work item belonging to the folder.

**Who picks it up:** each folder with a Feature tracker has a **lead session**,
chosen when the widget is added (e.g. product or architect). New cards go to
the lead as `proposed`. The lead assigns them to the right session with the
existing `duckterm session work update --assign`. The owner can also assign a
card directly from the board. Owner-created cards count as owner-sanctioned
work, so no relay-authority problem arises.

**Moving cards:**

- Agents move their own cards with the existing `work update` command. The
  board updates live over the existing event stream.
- The owner can drag cards. Dragging to Resolved asks for evidence, the same
  rule as today. Dragging back from Resolved reopens the item with a note.
- Opening a card shows its history, the linked requests, and the sessions
  involved.

## 4. Data and API changes

- Work items gain `folder` (the folder they belong to) and `description`.
- New state `in_qa`, reachable from `in_progress` and returning to
  `in_progress` if QA fails.
- Folder settings store: enabled widgets and the lead session.
- Routes: `GET /folders/:path` (overview data), `GET /work?folder=`,
  `POST /work` from the owner with `folder`, and `PATCH /folders/:path/widgets`.
- Mini Oracle: the existing Oracle question path with a folder scope that
  limits which sessions' digests it reads.

## 5. Relation to existing work

- **Work UI preview (main-dev, port 4393):** the collaboration design's
  "Work column" and this board show the same data. Proposal: the Feature
  tracker replaces the separate Work view, and a global Work view (if kept)
  is the same board across all folders.
- **AOS blueprints:** a blueprint can set up a folder with a Feature tracker
  and a lead session in one step.
- **Focus, Grid view, folder broadcast:** linked from the folder header, not
  duplicated.

## 6. Open decisions for the owner

1. Should clicking a folder name open the folder view (the chevron keeps
   expand/collapse), or should a separate button open it?
2. Who picks up new cards: a lead session per folder (recommended), or the
   owner always assigns?
3. Does the Feature tracker replace the separate Work view?
4. Should the folder view include subfolders' sessions (recommended), or only
   direct ones?

## 7. Out of scope for v1

Kanban for non-coding work, custom columns, widget plugins, cross-folder
boards, closing cards automatically when a GitHub PR merges (needs a trusted
integration, per the F12 design), and time estimates.

## Delivery

A visible UI change: the architect designs, the owner reviews a preview, then
it's built (backend by main-dev, UI by ui-dev) and released by release-dev.
