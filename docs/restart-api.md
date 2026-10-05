# Restart and seeded harness switches

`GET /sessions/:key/restart-options` reports the current harness/model,
`resume_restart: {available, reason?}`, and a `harnesses` array. Each entry has
`name`, `available`, optional `reason`, `models`, `model_source`
(`harness-reported` or `unknown`), `model_selection: {available, reason?}`,
`watchable`, and `context` (`native` or `seeded_new_conversation`). A failed
model-catalog lookup leaves manual model selection available where the adapter
supports it; `model_reason` explains the missing catalog.

Live-terminal, folder, archive, merge and transfer restrictions apply to all
paths. Exact conversation identity restricts only the current harness's resume
path. Draft protection is reported as `draft_clear`; `after_turn` indicates that
a verified parent-turn completion is still needed. These are a snapshot, not a
promise: POST and execution repeat the checks.

`POST /sessions/:key/restart` accepts `{harness?, model?}`. Omitting `harness`
retains the existing exact-resume behavior. Changing only the model also resumes
the same recorded conversation. Changing the harness starts a new conversation
on the same card, with the same key, project/worktree, folder, notes and task
links. The UI must explain that this is a new conversation seeded from a
checkpoint and saved notes; it does not transfer the old conversation. An empty
model on a switch uses the new harness's default, never the old harness's model.

The response is HTTP 202 with the existing restart-control fields, plus
`requested_harness`, `source_harness`, and `context`. A request stays `queued`
until a real parent Stop hook and a verified empty input box permit execution.
`DELETE /sessions/:key/restart` cancels a queued request. Existing
`GET /sessions/:key/restart` remains compatible and describes the exact-resume
path; the new dialog should use `restart-options` to discover switch paths.

Switch execution checkpoints before stopping, then rechecks activity and drafts.
The new launch uses fresh argv and no resume pointer. `previous_conversation`
retains the old native ID, harness, command, model, cwd and checkpoint reference
for recovery. It remains in native history; an unavailable old transcript cannot
be reconstructed by this operation. A failed spawn restores the stopped card's
old launch metadata rather than automatically starting another process.

The new runtime and its initially unknown native identity are saved atomically
in the existing session row/restart JSON, without a schema change. A per-process
launch-generation marker travels through the hook environment; only that
generation's matching native SessionStart can bind the ID. Missing hooks leave
`native_id_pending: true`, not a recycled ID. Delayed old hooks cannot change the
card's harness or identity. Successful launch (`status: completed`) does not by
itself claim that native identity or a transcript is available.

Claude Code, Copilot and legacy per-process Codex can be offered when installed.
Codex 0.159+ and unrecognized Codex version strings are unavailable for seeded
switches, with a shared-daemon identity prerequisite reason. This backend does
not implement the separately owned daemon resolver or unlock it based solely on
a version check. Copilot's model selector stays unsupported until its adapter
declares model arguments. No new external app window is opened.

POST/DELETE require owner authentication. Session bearer credentials cannot
access these owner routes. Remote UI/native transport must explicitly allow
`restart-options` and continue routing to the selected destination; the backend
does not change dashboard routing. Runtime compatibility and native UI acceptance
remain release checks; unit/fake-CLI tests do not prove a live provider version.
