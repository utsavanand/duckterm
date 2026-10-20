# Test Round 1 — Resume

**For:** the owner. Not a beta tester.
**Why you and not a tester:** only you can judge whether a resumed session
actually picked up the work. The acceptance is a judgement about continuity, not
a checkbox.
**Time:** about 30 minutes.

## Why this round exists

Measured in the live database, 2026-10-19:

    sessions restarted                     37
    restarts that carried memory forward    1
    harness switches attempted              2  (one was a model change)
    real cross-harness switches             1  — and it FAILED

**The product's core promise is that a session keeps its context. That has
happened once, and it did not work.**

Three weeks of memory work — five layered defects fixed, a 400-character
truncation bug, a file-descriptor leak, a storage-size fix shipped today — all
rest on a path with a sample size of one.

Everything built so far has been verified structurally: gates pass, fields are
populated, refs resolve, records are small. None of that tests whether a
resumed session can continue the work. That is what this round tests, and
nothing else.

## What we already know about the one failure

The single real harness switch, on `job-search`:

    source_harness     claude-code
    requested_harness  codex
    status             canceled
    code               launch_failed
    process_state      source_stopped
    memory             prepared, snapshot 884ad365...

**Memory preparation succeeded.** The failure was launching codex after the
source stopped. So the known defect is in the launch path, not the memory path.

That matters for how you read a failure in Case 3: if it fails the same way,
that is the old bug, not a memory bug.

## Prerequisites

- **v0.4.162 or later installed.** Check `duckterm --version`. This build
  carries the storage-size fix (`8889dba`); earlier builds write
  multi-megabyte checkpoint records.
- **A session with real work in it.** Not a fresh one. It needs enough history
  that resuming is a meaningful test — a session that has made decisions,
  hit problems, and has unfinished work.
- **Checkpoints are currently held.** You paused them until the size fix
  shipped. It has. Case 1 unholds them for one session.

---

## Case 1 — Take a checkpoint, and confirm it is small

### Steps

1. Pick one session with real history. Note its name.
2. Press **Checkpoint**.
3. Wait for it to finish.

### Acceptance criteria

- It completes. Not `failed`, not `unverified`, not `partial`.
- The stored record is **kilobytes, not megabytes**. Check:
  ```sh
  sqlite3 ~/.duckterm/db.sqlite "SELECT length(record_json) FROM checkpoints ORDER BY created_at DESC LIMIT 1;"
  sqlite3 ~/.duckterm/db.sqlite "SELECT length(text) FROM digest_items WHERE bucket='summary_revision_v1' ORDER BY updated_at DESC LIMIT 1;"
  ```
  Before the fix these were 8,371,642 and 5,111,562 bytes. Expect a few
  thousand now. **If either is in megabytes, stop — the fix did not take**, and
  nothing below is worth running.
- The record names what triggered it. New in v0.4.161; no previous checkpoint
  recorded this:
  ```sh
  sqlite3 ~/.duckterm/db.sqlite "SELECT json_extract(record_json,'\$.trigger') FROM checkpoints ORDER BY created_at DESC LIMIT 1;"
  ```
  It should say you asked for it, not be empty.

### Report

The three numbers, and whether the checkpoint reported success.

---

## Case 2 — Read the summary cold, and judge it

This is the acceptance test architect ratified, and it has never been run on a
fresh checkpoint.

### Steps

1. Read the summary just written:
   ```sh
   sqlite3 ~/.duckterm/db.sqlite "SELECT json_extract(text,'\$.summary') FROM digest_items WHERE bucket='summary_revision_v1' ORDER BY updated_at DESC LIMIT 1;"
   ```
2. Read it as though you had never seen that session.

### Acceptance criteria

- **You can state what the session should do next, from the summary alone.**
  Not "I know because I was there" — from the text in front of you.
- It describes what was **concluded**, not a list of what happened. A summary
  that says "covers four inbox prompts and some QA work" is a receipt, and a
  FAIL.
- Anything it says is unfinished or blocked is actually unfinished or blocked.

### Report

- Could you state a next action? Quote the sentence that gave it to you.
- Was anything in it wrong, or stale?

**Known comparison:** of 13 saved summaries measured on 2026-10-19, 9 let a
reader state a next action and 4 were purely retrospective. Which kind is this?

---

## Case 3 — Actually resume

The real test. Everything above is preparation.

### Steps

1. Restart the session you checkpointed — same harness, not a switch. Use
   whatever restart path the app offers.
2. When it comes back, **do not re-explain the work.** Ask it something that
   requires the prior context, for example:
   - "What were you working on?"
   - "What is blocked right now?"
   - "What should you do next?"

### Acceptance criteria

- The session returns and is usable.
- It answers from the handoff, **not** by saying it has no context or asking you
  to explain.
- Its answer matches what the session was actually doing. A confident, plausible,
  **wrong** answer is a worse failure than admitting no context — note it loudly
  if that happens.
- Anything it names as unfinished is genuinely unfinished.

### Report

- Did it know what it had been doing?
- Did it invent anything? Quote it.
- **Could you keep working with it**, or did you have to re-brief it?

That last question is the whole round. Everything else is diagnostics.

---

## Case 4 — Switch harness (optional, and expected to be rough)

Only if Cases 1–3 passed. This is the path that failed before.

### Steps

1. On a session you do not mind disrupting, request a harness switch —
   Claude Code to Codex or the reverse.

### Acceptance criteria

- Memory preparation succeeds (it did last time — that half worked).
- **The new harness actually launches.** The previous failure was
  `code: launch_failed`, `process_state: source_stopped`.
- The new session can answer the same context questions as Case 3.

### Report

- If it fails, the exact `status`, `code` and `process_state`:
  ```sh
  sqlite3 ~/.duckterm/db.sqlite "SELECT name, json_extract(restart_json,'\$.status'), json_extract(restart_json,'\$.code'), json_extract(restart_json,'\$.process_state') FROM sessions WHERE restart_json LIKE '%harness%';"
  ```
- **If it fails with `launch_failed` again, that is the known bug and not news.**
  What would be news: failing differently, or launching but resuming with no
  context.

---

## What this round decides

**If Case 3 passes**, the memory feature works end to end for the first time.
That is the point at which it is reasonable to tell anyone memory is done.

**If Case 3 fails**, we learn which half — writing or reading — and the three
weeks of work have a target instead of a direction.

Either outcome is worth more than another structural gate.

### Not in scope

Retrieval ranking, the staleness clock, the artifacts explorer, compaction. All
in flight with the fleet. This round tests one thing: can a session resume.
