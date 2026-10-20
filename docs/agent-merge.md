# Merge with agent

Right-click a session, choose **Merge with agent**, and select a destination on
that session's computer. The destination needs an inbox in a shared sidebar
folder. The owner can select a destination in another folder; this does not
change either session's folder or peer-sharing permissions.

Choose the material to include, then review before sending:

- **Conversation context:** an editable saved summary. This is a handoff of the
  reviewed text; it does not splice native conversation histories or promise
  that the saved summary covers the latest turn. If no summary is available,
  the owner can write the context to send.
- **Notes:** the reviewed text is appended to the destination's current notes,
  preserving edits made there while the dialog was open.
- **Code from the other worktree:** an explicit request for the destination
  agent to integrate the reviewed source commit. Both worktrees must belong to
  the same Git repository on that computer and have no uncommitted changes.
  Shared worktrees, unrelated repositories and already-contained commits explain
  why the option is unavailable. No files, branches or indexes are changed by
  DuckTerm during this operation.

The recipient rechecks newer work and conflicts before integrating code. Delivery
or acknowledgement is not evidence that Git integration succeeded: inspect the
recipient's reply and work. The request does not authorize resetting worktrees,
losing local edits, removing worktrees or publishing changes.

Both agents stay open. Their project folders and native conversation identities
are preserved. The existing fork-to-parent **Merge back** action remains available
with its existing close-child choice.

A busy destination receives a reminder after its turn; unsupported runtimes get
inbox delivery only. Stopped agents retain their inbox request for later. Recent
requests and recipient replies are available when reopening **Merge with agent**
on either participant. Cross-computer merges are not available in this version.
Remote hosts and the Mac app must support the new endpoints.

## Durability and review

Notes, the immutable merge receipt and the owner inbox message commit in one
SQLite transaction using the existing delivery broker. Unchanged retries reuse
one request key, including after a lost HTTP response. Changing the request with
the same key is rejected. A previously saved request can be recovered even if the
source later changes. Refreshing a stale preview retains the owner's edited text
for another review.

Before a new send, the server rechecks destination eligibility and source context,
notes and Git evidence. Git inspection runs off the server event loop and executes
read-only commands. The UI separates saved/delivered/acknowledged state from code
integration. Neither agent is stopped, resumed, archived, renamed or reparented.
