import { SessionConversationRecovery } from "./ConversationRecovery";
import { identityBlocksResume } from "./conversationRecoveryState";
import type { ConversationIdentity } from "./conversationRecoveryState";
import { requestArchive } from "./ArchiveUndo";
import { useCallback, useEffect, useMemo, useState } from "react";
import { ForkMergeDialog } from "./ForkMergeDialog";
import { api, forkMergeService } from "./api";
import { desktop, destinationRequest, selectLaunchTarget } from "./desktop";
import { splitSessionRef } from "./hostTransport";
import { effectiveState } from "./sessions";
import { SessionView } from "./types";
import { useResumeSession } from "./useResumeSession";
import { useToast } from "./ui";
import "./sessionCard.css";
import { RestartControls } from "./RestartControls";

// Session actions share one home in every sidebar density.
export function SessionCard({ session: s, now, onFork, onDelete, onRename, onUngroup }: {
  session: SessionView; now: number;
  onFork: (key: string) => void;
  onDelete: (key: string) => Promise<boolean>;
  onRename: (key: string, name: string) => void;
  onUngroup?: () => Promise<void>;
}) {
  const toast = useToast();
  const [recovery, setRecovery] = useState<{ key: string; identity: ConversationIdentity } | null>(null);
  const updateRecovery = useCallback((identity: ConversationIdentity) => setRecovery({ key: s.key, identity }), [s.key]);
  const resumeBlocked = recovery?.key === s.key ? !recovery.identity.canResume : identityBlocksResume(s);
  const effState = effectiveState(s, now);
  const archived = effState === "archived" || effState === "merged";
  const [merging, setMerging] = useState(false);
  const mergeService = useMemo(() => forkMergeService(s.key), [s.key]);
  const ended = effState === "terminated";
  const live = !["terminated", "stopped", "interrupted", "archived", "merged"].includes(effState);
  const resumable = ["stopped", "interrupted", "terminated"].includes(effState) && s.launched;
  const canStop = live && s.launched;
  const canArchive = !archived && s.launched;
  const canBranch = live && (Boolean(s.branch) || s.runtime === "claude-code");
  const [notesOpen, setNotesOpen] = useState(false);
  const [notes, setNotes] = useState(s.notes ?? "");
  const [savedNotes, setSavedNotes] = useState(s.notes ?? "");
  const [capturing, setCapturing] = useState(false);
  const [ending, setEnding] = useState(false);
  const { resuming, resumeSession } = useResumeSession(s.key);
  const [archiving, setArchiving] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [renaming, setRenaming] = useState(false);
  const [draft, setDraft] = useState(s.label);
  async function saveRename() {
    const name = draft.trim();
    if (!name || name === s.label) { setRenaming(false); return; }
    try {
      await api.updateSession(s.key, { name });
      onRename(s.key, name);
      setRenaming(false);
      toast("Renamed");
    } catch (e) { toast(`Rename failed: ${(e as Error).message}`, "err"); }
  }
  async function act(label: string, fn: () => Promise<unknown>) {
    try {
      await fn();
      toast(label);
    } catch (e) {
      toast(`${label} failed: ${(e as Error).message}`, "err");
    }
  }

  async function saveNotes() {
    if (notes === savedNotes) return;
    try {
      await api.updateSession(s.key, { notes });
      setSavedNotes(notes);
      setNotesOpen(false);
      toast("Notes saved");
    } catch (e) { toast(`Notes failed: ${(e as Error).message}`, "err"); }
  }

  async function stopSession() {
    if (ending) return;
    setEnding(true);
    try {
      await api.stop(s.key);
      toast("Stopped");
      setEnding(false);
    } catch (e) {
      toast(`Stop failed: ${(e as Error).message}`, "err");
      setEnding(false); // let the user retry
    }
  }

  // Disarm the delete confirmation if the user doesn't follow through quickly.
  useEffect(() => {
    if (!confirmDelete) return;
    const t = setTimeout(() => setConfirmDelete(false), 4000);
    return () => clearTimeout(t);
  }, [confirmDelete]);

  async function requestDelete() {
    if (ending) return;
    if (!confirmDelete) {
      setConfirmDelete(true); // first click: arm
      return;
    }
    setConfirmDelete(false);
    setEnding(true);
    const deleted = await onDelete(s.key);
    if (!deleted) setEnding(false); // cancelled (e.g. unmerged confirm) or failed
  }

  async function archiveSession() {
    if (archiving) return;
    setArchiving(true);
    try {
      await requestArchive(s.key);
      // The archive event removes it from this view; no need to un-set.
    } catch (e) {
      toast(`Archive failed: ${(e as Error).message}`, "err");
      setArchiving(false);
    }
  }

  async function captureCheckpoint() {
    if (capturing) return;
    // Capturing runs a summarizer agent (claude -p / codex / copilot), a few
    // seconds — show a spinner so the click doesn't feel dead.
    setCapturing(true);
    try {
      await act("Checkpoint recorded", () => api.checkpoint(s.key, "manual"));
    } finally {
      setCapturing(false);
    }
  }

  return <section className="rd-session-controls" aria-label="Session controls">
    {merging && <ForkMergeDialog service={mergeService} onClose={() => setMerging(false)} onSaved={() => toast("Merge summary saved")} />}
    <div className="rd-session-controls-identity">
      <div className="rd-session-controls-caption">Session</div>
      <div className="rd-session-controls-title">
        {renaming ? <form onSubmit={(e) => { e.preventDefault(); void saveRename(); }}>
          <input aria-label="Session name" autoFocus value={draft} onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Escape") setRenaming(false); }} />
          <button className="rd-btn rd-btn-sm" type="submit">Save</button>
          <button className="rd-btn rd-btn-sm" type="button" onClick={() => setRenaming(false)}>Cancel</button>
        </form> : <strong>{s.label}</strong>}
        <span className={`rd-state st-${effState}`}>{effState}</span>
      </div>
      <div className="rd-session-controls-folder"><span>{s.group || "Ungrouped"}</span>
        {!ended && <button className="rd-session-controls-link" onClick={() => { setDraft(s.label); setRenaming(true); }}>Rename</button>}
      </div>
    </div>
    <dl className="rd-session-controls-meta"><div><dt>Harness</dt><dd>{s.runtime ?? "—"}</dd></div>
      <div><dt>Model</dt><dd>{s.model ?? "Not reported yet"}</dd></div></dl>
    {["claude-code", "codex", "copilot"].includes(s.runtime ?? "") && <SessionConversationRecovery key={s.key} session={s} stopped={!live && !archived} onIdentity={updateRecovery} />}
    <fieldset className="rd-session-controls-actions" disabled={ending || archiving || resuming}>
          {s.launched && <RestartControls key={s.key} session={s} showActions={live} />}
          {s.parentKey && !archived && <button className="rd-btn rd-btn-sm" onClick={() => setMerging(true)}>Merge back</button>}
          {resumable && (
            <button
              className="rd-btn rd-btn-sm rd-btn-primary"
              title="Relaunch this session — continues the conversation for Claude Code"
              disabled={resuming || resumeBlocked}
              onClick={resumeSession}
            >
              {resuming ? (
                <span className="rd-inline-spin">
                  <span className="rd-spinner" />
                  Resuming…
                </span>
              ) : (
                "Resume"
              )}
            </button>
          )}
          {!archived && !ended && (
            <button
              className="rd-btn rd-btn-sm rd-btn-ghost"
              title="Record what was done so far"
              disabled={capturing}
              onClick={captureCheckpoint}
            >
              {capturing ? (
                <span className="rd-inline-spin">
                  <span className="rd-spinner" />
                  Capturing…
                </span>
              ) : (
                "Checkpoint"
              )}
            </button>
          )}
          {/* One branching action: the modal offers a git worktree fork (or
              promotes an in-place session onto a branch) and, for claude-code,
              a conversation-only fork. */}
          {!archived && !ended && canBranch && (
            <button
              className="rd-btn rd-btn-sm rd-btn-ghost"
              title="Fork this session — into a git worktree, or fork the conversation"
              onClick={() => onFork(s.key)}
            >
              Fork
            </button>
          )}
          {!archived && !ended && (
            <button
              className={`rd-btn rd-btn-sm rd-btn-ghost${notesOpen ? " active" : ""}`}
              title="Personal notes for this session (local only)"
              onClick={() => setNotesOpen((o) => !o)}
            >
              Notes{savedNotes ? " •" : ""}
            </button>
          )}
          {onUngroup && (
            <button
              className="rd-btn rd-btn-sm rd-btn-ghost"
              title="Move this session out of its folder (dragging onto UNGROUPED works too)"
              onClick={() => { void act("Removed from folder", onUngroup); }}
            >
              Ungroup
            </button>
          )}
          {resumable && splitSessionRef(s.key).host === "local" && desktop()?.currentTarget === "local" && ["claude-code", "codex"].includes(s.runtime ?? "") && <>
            <button className="rd-btn rd-btn-sm rd-btn-ghost" onClick={() => window.dispatchEvent(new CustomEvent("move-to-remote", { detail: s.key }))}>Move to remote…</button>
            <button className="rd-btn rd-btn-sm rd-btn-ghost" onClick={async () => {
              if (!window.confirm("Continue this session locally as a separate continuation? A remote session, if created, will remain running.")) return;
              try {
                await destinationRequest("local", "project-continue", { source_session: s.key });
                localStorage.removeItem(`moved-session:${s.key}`);
                await resumeSession();
              } catch (e) { toast(`Continue failed: ${(e as Error).message}`, "err"); }
            }}>Continue locally</button>
            {(s.remoteTransfer?.stage === "moved" || localStorage.getItem(`moved-session:${s.key}`)) && <button className="rd-btn rd-btn-sm rd-btn-ghost" onClick={() => {
              const moved = s.remoteTransfer?.stage === "moved" ? s.remoteTransfer : JSON.parse(localStorage.getItem(`moved-session:${s.key}`)!);
              selectLaunchTarget(moved.target, {}, moved.session_key ?? moved.key);
            }}>Open remote session</button>}
          </>}
          {canStop && (
            <button
              className="rd-btn rd-btn-sm rd-btn-danger"
              disabled={ending}
              onClick={stopSession}
            >
              {ending ? (
                <span className="rd-inline-spin">
                  <span className="rd-spinner" />
                  Stopping…
                </span>
              ) : (
                "Stop"
              )}
            </button>
          )}
          {canArchive && (
            <button
              className="rd-btn rd-btn-sm rd-btn-ghost"
              title="Archive for good — history is kept, but the session leaves the list and can't be resumed (Stop is the pause)"
              disabled={archiving}
              onClick={archiveSession}
            >
              {archiving ? (
                <span className="rd-inline-spin">
                  <span className="rd-spinner" />
                  Archiving…
                </span>
              ) : (
                "Archive"
              )}
            </button>
          )}
    </fieldset>
        {notesOpen && (
          <div className="rd-row-notes-wrap">
            <textarea
              aria-label="Session notes"
              className="rd-row-notes"
              value={notes}
              placeholder="Notes for this session (local only)…"
              onChange={(e) => setNotes(e.target.value)}
              rows={2}
            />
            <div className="rd-row-notes-bar">
              <span className="hint">
                {notes !== savedNotes ? "Unsaved changes" : "Saved"}
              </span>
              <button
                className="rd-btn rd-btn-sm rd-btn-primary"
                disabled={notes === savedNotes}
                onClick={saveNotes}
              >
                Save
              </button>
            </div>
          </div>
        )}

    <fieldset className="rd-session-controls-danger" disabled={ending || archiving || resuming}>
          <button
            className={`rd-btn rd-btn-sm rd-btn-danger${confirmDelete ? " armed" : ""}`}
            title={
              confirmDelete
                ? "Click again to confirm"
                : s.launched || !live
                  ? "Delete this session and its history"
                  : "Stop watching — remove it from the dashboard (the agent keeps running in its own terminal)"
            }
            disabled={ending}
            onClick={requestDelete}
          >
            {confirmDelete
              ? "Confirm?"
              : s.launched || !live
                ? "Delete"
                : "Stop watching"}
          </button>
    </fieldset>
  </section>;
}
