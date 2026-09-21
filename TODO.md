# TODO

- **Re-home approvals.** The right-panel approvals box is removed (2026-09-20)
  — as a sticky expandable card it overlapped the panel content, and the user's
  real workflow is answering in the session's terminal anyway. Ideas for a
  better home when it comes back:
  - a banner strip at the top of the SELECTED session's terminal pane (the
    approval belongs to the session you're looking at);
  - browser/desktop notification with Approve/Deny actions;
  - a dedicated "needs you" filter in the left panel instead of a box.
  Backend is untouched (registry, decide endpoints, zombie expiry, the
  Approve/Deny e2e via API) — this is a UI-placement question only.
