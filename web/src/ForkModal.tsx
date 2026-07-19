import { useState } from "react";
import { api } from "./api";
import { SessionView } from "./types";
import { Button, Field, inputStyle, Modal, useToast } from "./ui";

// Forking a session creates a NEW git worktree on a NEW branch, taken off the
// parent's branch, and opens the forked agent in a terminal you can drive.
export function ForkModal({
  session,
  onClose,
}: {
  session: SessionView;
  onClose: () => void;
}) {
  const toast = useToast();
  const canConversationFork = session.runtime === "claude-code";
  // A session already on a branch forks off it; one without a branch but in a
  // folder gets promoted onto a fresh worktree. Either way, "worktree" is an
  // option whenever there's a repo to branch (the server rejects non-git).
  const hasBranch = Boolean(session.branch);
  const canWorktreeFork = hasBranch || Boolean(session.cwd);
  // Default to whichever the session supports; worktree if both.
  const [mode, setMode] = useState<"worktree" | "conversation">(
    canWorktreeFork ? "worktree" : "conversation",
  );
  const [branch, setBranch] = useState(
    session.branch ? `${session.branch}-fork` : "",
  );
  const [command, setCommand] = useState("claude");
  const [busy, setBusy] = useState(false);
  // Carry the parent's conversation into the worktree fork — only for harnesses
  // that can resume (Claude Code, Copilot). On by default when available.
  const canCarryContext =
    session.runtime === "claude-code" || session.runtime === "copilot";
  const [carryContext, setCarryContext] = useState(canCarryContext);

  // Forks run in a PTY Duckterm owns (in_terminal:false) so they render in the
  // dashboard's terminal pane, same as launches — no external terminal window.
  async function submit() {
    setBusy(true);
    try {
      if (mode === "conversation") {
        await api.forkConversation(session.key);
        toast("Forked conversation running in Duckterm");
      } else if (hasBranch) {
        // True fork: branch off the parent's branch, open a new agent.
        const r = await api.fork(session.key, {
          command,
          branch: branch || undefined,
          in_terminal: false,
          carry_context: canCarryContext && carryContext,
        });
        const ctx = r.carried_context ? " with its conversation" : "";
        toast(`Fork running on ${r.branch}${ctx}`);
      } else {
        // No branch yet: promote this in-place session onto a new worktree.
        const r = await api.promote(session.key, {
          branch: branch || undefined,
        });
        toast(`Worktree created on ${r.branch}`);
      }
      onClose();
    } catch (e) {
      toast(`Fork failed: ${(e as Error).message}`, "err");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal title={`Fork ${session.label}`} onClose={onClose}>
      <Field label="What kind of fork?">
        <label
          className="rd-radio"
          style={{ opacity: canWorktreeFork ? 1 : 0.5 }}
        >
          <input
            type="radio"
            checked={mode === "worktree"}
            disabled={!canWorktreeFork}
            onChange={() => setMode("worktree")}
          />
          <span>
            <strong>Git worktree</strong> —{" "}
            {hasBranch ? (
              <>
                branch off{" "}
                <code className="rd-inline-code">{session.branch}</code> into a
                new checkout so the fork's code is isolated
              </>
            ) : (
              "create a worktree + branch from this session's folder"
            )}
            {!canWorktreeFork && " (this session has no folder to branch)"}
          </span>
        </label>
        <label
          className="rd-radio"
          style={{ opacity: canConversationFork ? 1 : 0.5 }}
        >
          <input
            type="radio"
            checked={mode === "conversation"}
            disabled={!canConversationFork}
            onChange={() => setMode("conversation")}
          />
          <span>
            <strong>Conversation only</strong> — continue the agent's
            conversation in a new Duckterm terminal, no git branch
            {!canConversationFork && " (Claude Code sessions only)"}
          </span>
        </label>
      </Field>

      {mode === "worktree" && (
        <>
          <Field label="New branch">
            <input
              style={inputStyle}
              value={branch}
              onChange={(e) => setBranch(e.target.value)}
              placeholder="feature/login-v2"
            />
          </Field>
          {hasBranch && (
            <Field label="Agent command">
              <input
                style={inputStyle}
                value={command}
                onChange={(e) => setCommand(e.target.value)}
              />
            </Field>
          )}
          {canCarryContext && (
            <label className="rd-radio">
              <input
                type="checkbox"
                checked={carryContext}
                onChange={(e) => setCarryContext(e.target.checked)}
              />
              <span>
                <strong>Carry the conversation</strong> — the fork continues
                this session's chat in the new worktree, instead of starting
                fresh.
              </span>
            </label>
          )}
        </>
      )}
      <div
        style={{
          display: "flex",
          justifyContent: "flex-end",
          gap: 8,
          marginTop: 8,
        }}
      >
        <Button variant="ghost" onClick={onClose}>
          Cancel
        </Button>
        <Button onClick={submit} disabled={busy}>
          {busy ? "Forking…" : "Create fork"}
        </Button>
      </div>
    </Modal>
  );
}
