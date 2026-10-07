import { useState } from "react";
import { createPortal } from "react-dom";
import { requestArchive } from "./ArchiveUndo";
import { ForkMergeDialog } from "./ForkMergeDialog";
import { api, forkMergeService } from "./api";
import { desktop, destinationRequest, selectLaunchTarget } from "./desktop";
import { splitSessionRef } from "./hostTransport";
import { effectiveState } from "./sessions";
import { identityBlocksResume } from "./conversationRecoveryState";
import { checkpointNotice } from "./checkpointState";
import { SessionView } from "./types";
import { useResumeSession } from "./useResumeSession";
import { useNow } from "./useNow";
import { Modal, useToast } from "./ui";
import { RestartControls } from "./RestartControls";
import { SessionActionMenu, type SessionActionAnchor } from "./SessionActionMenu";

export function SessionActions({ session: s, anchor, onClose, onFork, onDelete, onRename, onNotes, onUngroup }: {
  session: SessionView; anchor: SessionActionAnchor; onClose: () => void;
  onFork: (key: string) => void; onDelete: (key: string) => Promise<boolean>;
  onRename: (key: string, name: string) => void; onNotes: (key: string) => void;
  onUngroup?: () => Promise<void>;
}) {
  const toast = useToast();
  const state = effectiveState(s, useNow());
  const archived = ["archived", "merged"].includes(state);
  const ended = state === "terminated";
  const live = !["stopped", "interrupted", "terminated", "archived", "merged"].includes(state);
  const resumable = !live && !archived && s.launched;
  const [busy, setBusy] = useState("");
  const [dialog, setDialog] = useState<"rename" | "delete" | "merge" | null>(null);
  const [restartExpanded, setRestartExpanded] = useState(false);
  const [draft, setDraft] = useState(s.label);
  const { resuming, recoveryBlocked, resumeSession } = useResumeSession(s.key);
  const disabled = !!busy || resuming;
  async function act(label: string, action: () => Promise<unknown>) {
    if (disabled) return;
    setBusy(label);
    try { const result = await action(); toast(typeof result === "string" ? result : label); onClose(); }
    catch (error) { toast(`${label} failed: ${(error as Error).message}`, "err"); }
    finally { setBusy(""); }
  }
  const item = (label: string, action: () => void, danger = false) => <button role="menuitem" className={danger ? "rd-btn-danger" : ""} disabled={disabled} onClick={action}>{label}</button>;
  const canMove = resumable && splitSessionRef(s.key).host === "local" && desktop()?.currentTarget === "local" && ["claude-code", "codex"].includes(s.runtime ?? "");
  return <>
    <SessionActionMenu anchor={anchor} label={s.label} expanded={!!dialog || restartExpanded} onClose={onClose}>
      {s.launched && <fieldset disabled={disabled}><RestartControls session={s} showActions={live} menu onExpanded={setRestartExpanded} onActionComplete={onClose} /></fieldset>}
      {resumable && <button role="menuitem" disabled={disabled || recoveryBlocked || identityBlocksResume(s)}
        title={identityBlocksResume(s) ? "Choose a conversation in the session’s recovery panel before resuming" : undefined}
        onClick={async () => { await resumeSession(); onClose(); }}>{resuming ? "Resuming…" : "Resume"}</button>}
      {!archived && !ended && item(busy === "Checkpoint" ? "Capturing…" : "Checkpoint", () => void act("Checkpoint", async () => {
        const cp = await api.checkpoint(s.key, "manual");
        window.dispatchEvent(new CustomEvent("duckterm-checkpoint", { detail: s.key }));
        return checkpointNotice(cp);
      }))}
      {live && (s.branch || s.runtime === "claude-code") && item("Fork…", () => { onFork(s.key); onClose(); })}
      {s.parentKey && !archived && item("Merge back…", () => setDialog("merge"))}
      <hr role="separator" />
      {!archived && !ended && item("Rename…", () => setDialog("rename"))}
      {item("Notes", () => { onNotes(s.key); onClose(); })}
      {onUngroup && !archived && item("Remove from folder", () => void act("Removed from folder", onUngroup))}
      {canMove && <>
        <hr role="separator" />
        {item("Move to remote…", () => { window.dispatchEvent(new CustomEvent("move-to-remote", { detail: s.key })); onClose(); })}
        {item("Continue locally", () => {
          if (!window.confirm("Continue this session locally as a separate continuation? A remote session, if created, will remain running.")) return;
          void act("Continuing locally", async () => { await destinationRequest("local", "project-continue", { source_session: s.key }); localStorage.removeItem(`moved-session:${s.key}`); await resumeSession(); });
        })}
        {(s.remoteTransfer?.stage === "moved" || localStorage.getItem(`moved-session:${s.key}`)) && item("Open remote session", () => {
          const moved = s.remoteTransfer?.stage === "moved" ? s.remoteTransfer : JSON.parse(localStorage.getItem(`moved-session:${s.key}`)!);
          selectLaunchTarget(moved.target, {}, moved.session_key ?? moved.key); onClose();
        })}
      </>}
      {(s.launched || archived || ended || !live) && <hr role="separator" />}
      {live && s.launched && item(busy === "Stopped" ? "Stopping…" : "Stop", () => void act("Stopped", () => api.stop(s.key)), true)}
      {!archived && s.launched && item("Archive", () => void act("Archived — Undo is available", () => requestArchive(s.key)))}
      {(archived || ended) && item("Delete permanently…", () => setDialog("delete"), true)}
      {!s.launched && !ended && !archived && item("Stop watching…", () => setDialog("delete"))}
    </SessionActionMenu>
    {dialog === "merge" && <ForkMergeDialog service={forkMergeService(s.key)} onClose={onClose} onSaved={() => toast("Merge summary saved")} />}
    {(dialog === "rename" || dialog === "delete") && createPortal(<Modal title={dialog === "rename" ? "Rename session" : !s.launched && live ? "Stop watching session" : "Delete session permanently"} onClose={onClose}>
      <form className="rd-session-action-dialog" role="dialog" aria-label={dialog === "rename" ? "Rename session" : "Confirm session removal"} aria-modal="true" onKeyDown={e => {
        if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); onClose(); }
        if (e.key === "Tab") {
          const items = [...e.currentTarget.querySelectorAll<HTMLElement>("input:not(:disabled),button:not(:disabled)")];
          const first = items[0], last = items[items.length - 1];
          if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last?.focus(); }
          else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first?.focus(); }
        }
      }} onSubmit={async e => {
        e.preventDefault(); if (disabled) return;
        if (dialog === "rename") { if (draft.trim()) await act("Renamed", async () => { await api.updateSession(s.key, { name: draft.trim() }); onRename(s.key, draft.trim()); }); }
        else { setBusy("Deleting"); try { if (await onDelete(s.key)) onClose(); } finally { setBusy(""); } }
      }}><p>{s.label}</p>
        {dialog === "rename" ? <input autoFocus aria-label="Session name" value={draft} onChange={e => setDraft(e.target.value)} /> : <p>{!s.launched && live ? "Remove this session and its DuckTerm history from the dashboard. Its external agent keeps running." : "Remove this session, its DuckTerm history and its managed worktree, if any. This cannot be undone."}</p>}
        <footer><button autoFocus={dialog === "delete"} type="button" className="rd-btn" onClick={onClose}>Cancel</button><button className={`rd-btn ${dialog === "delete" ? "rd-btn-danger" : "rd-btn-primary"}`} disabled={disabled || (dialog === "rename" && !draft.trim())}>{busy || (dialog === "rename" ? "Save" : !s.launched && live ? "Stop watching" : "Delete permanently")}</button></footer>
      </form></Modal>, document.body)}
  </>;
}
