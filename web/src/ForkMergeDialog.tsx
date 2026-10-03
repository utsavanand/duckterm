import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { Modal } from "./ui";
import "./forkMerge.css";

export type MergeDelivery = "pending next turn" | "inbox only" | "delivered" | "acknowledged" | "cancelled";
export interface ForkMergePreview {
  child: { key: string; name: string; branch?: string; commit?: string };
  parent: { key: string; name: string; model?: string; state: string; priorityDelivery: boolean } | null;
  summary: string;
  allowed: boolean;
  reason?: string;
}
export interface ForkMergeRecord {
  id: string;
  summary: string;
  keepOpen: boolean;
  statusAvailable?: boolean;
  childClosed?: boolean;
  delivery: MergeDelivery;
  checkpoint: "pending" | "created" | "not created";
}
export interface ForkMergeService {
  preview: () => Promise<ForkMergePreview>;
  send: (draft: { summary: string; keepOpen: boolean; requestKey: string }) => Promise<ForkMergeRecord>;
  status: (id: string) => Promise<ForkMergeRecord>;
}

/** The service adapter maps the shared delivery broker; the dialog never injects terminal text. */
export function ForkMergeDialog({ service, onClose, onSaved }: {
  service: ForkMergeService; onClose: () => void; onSaved: (record: ForkMergeRecord) => void;
}) {
  const [preview, setPreview] = useState<ForkMergePreview | null>(null);
  const [summary, setSummary] = useState("");
  const [keepOpen, setKeepOpen] = useState(false);
  const [record, setRecord] = useState<ForkMergeRecord | null>(null);
  const [error, setError] = useState("");
  const [statusError, setStatusError] = useState("");
  const [loading, setLoading] = useState(true), [busy, setBusy] = useState(false), [retry, setRetry] = useState(0);
  const active = useRef(false), sending = useRef(false);
  const submission = useRef<{ signature: string; key: string }>();
  const editor = useRef<HTMLTextAreaElement>(null);
  const dialog = useRef<HTMLDivElement>(null);
  const opener = useRef(document.activeElement as HTMLElement | null);
  const recordId = record?.id;
  useEffect(() => { const prior = opener.current; active.current = true; return () => { active.current = false; prior?.focus(); }; }, []);
  useEffect(() => {
    let live = true;
    setLoading(true); setError("");
    void service.preview().then(value => {
      if (live) { setPreview(value); setSummary(value.summary); }
    }).catch((cause: Error) => { if (live) setError(`Couldn’t load this fork: ${cause.message}`); })
      .finally(() => { if (live) setLoading(false); });
    return () => { live = false; };
  }, [service, retry]);
  useEffect(() => { if (!loading) editor.current?.focus(); }, [loading]);
  useEffect(() => {
    if (!recordId) return;
    let live = true, running = false;
    let timer: ReturnType<typeof setTimeout>;
    const visible = () => document.visibilityState !== "hidden";
    async function refresh() {
      if (!live || running || !visible()) return;
      running = true;
      try {
        const value = await service.status(recordId!);
        if (live) { setRecord(value); setStatusError(""); }
      } catch (cause) { if (live) setStatusError(`Couldn’t refresh delivery: ${(cause as Error).message}. Showing the last known state.`); }
      finally { running = false; if (live && visible()) timer = setTimeout(() => void refresh(), 3000); }
    }
    const visibility = () => { clearTimeout(timer); if (document.visibilityState !== "hidden") void refresh(); };
    document.addEventListener("visibilitychange", visibility); void refresh();
    return () => { live = false; clearTimeout(timer); document.removeEventListener("visibilitychange", visibility); };
  }, [service, recordId]);
  async function send() {
    if (sending.current || !preview?.allowed || !preview.parent || !summary.trim()) return;
    const signature = JSON.stringify([preview.child.key, preview.parent.key, summary, keepOpen]);
    if (submission.current?.signature !== signature) submission.current = { signature, key: crypto.randomUUID() };
    sending.current = true; setBusy(true); setError("");
    try {
      const result = await service.send({ summary, keepOpen, requestKey: submission.current.key });
      if (active.current) { setRecord(result); onSaved(result); }
    } catch (cause) {
      if (active.current) setError(`Couldn’t confirm the merge: ${(cause as Error).message}. Your edits are kept. Retry unchanged to check the original send; the note may already be saved.`);
    } finally { sending.current = false; if (active.current) setBusy(false); }
  }
  const close = () => { if (!sending.current) onClose(); };
  return createPortal(<Modal title="Merge back to parent" onClose={close} width={660}>
    <div ref={dialog} className="rd-merge-review" role="dialog" aria-modal="true" aria-label="Merge back to parent" onKeyDown={event => {
      if (event.key === "Escape") { event.stopPropagation(); close(); }
      if (event.key !== "Tab") return;
      const controls = [...dialog.current!.querySelectorAll<HTMLElement>("textarea:not(:disabled), input:not(:disabled), button:not(:disabled)")];
      const first = controls[0], last = controls[controls.length - 1];
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
      if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
    }}>
      {loading && <p role="status">Preparing the fork summary…</p>}
      {preview && <>
        <div className="rd-merge-route"><strong>{preview.child.name}</strong><span aria-hidden="true">→</span><strong>{preview.parent?.name ?? "Parent deleted"}</strong>{preview.parent?.model && <span>{preview.parent.model}</span>}</div>
        {!record && <>
          <label className="rd-merge-label" htmlFor="fork-merge-summary">Summary to send <small>Your edited text is sent exactly as shown.</small></label>
          <textarea ref={editor} id="fork-merge-summary" value={summary} disabled={busy || !preview.allowed} maxLength={16384} onChange={event => setSummary(event.target.value)} />
          <p>This sends a summary of the fork, not the full thread.</p>
          {preview.child.branch && <div className="rd-merge-worktree"><code>{preview.child.branch}{preview.child.commit && ` · ${preview.child.commit}`}</code><p>Code changes go through git or a pull request. This action does not merge code.</p></div>}
          <label className="rd-merge-keep"><input type="checkbox" checked={keepOpen} disabled={busy} onChange={event => setKeepOpen(event.target.checked)} />Keep child open</label>
          <p>{keepOpen ? "Continue working in the child and send another summary later." : "After the note is saved, the child closes in a readable, non-resumable merged state."}</p>
          {preview.parent && <p>{!preview.parent.priorityDelivery ? "This parent receives inbox mail only; automatic delivery is unavailable." : preview.parent.state === "busy" ? "The parent finishes its current turn before receiving the note." : "The note can reach an idle parent immediately."}</p>}
          <p>A merge checkpoint is created on the parent only when the note is delivered.</p>
          {!preview.allowed && <p role="alert">{preview.reason || "This fork cannot be merged into its original parent."}</p>}
        </>}
        {record && <section className="rd-merge-result" aria-label="Saved merge summary"><div className="rd-merge-delivery">Delivery to {preview.parent?.name ?? "parent"}<span>{record.delivery}</span></div><pre>{record.summary}</pre><p>{record.keepOpen ? "Child remains open." : "Child is merged and remains readable."} Both histories record this merge.</p><p>{record.checkpoint === "created" ? "Parent merge checkpoint created." : record.checkpoint === "pending" ? "Parent merge checkpoint will be created at delivery." : "No parent checkpoint was created."}</p>{record?.statusAvailable === false && <p role="status">Delivery tracking has retired. Showing the last recorded state.</p>}
      {statusError && <p role="status">{statusError}</p>}</section>}
      </>}
      {error && <p className="rd-merge-error" role="alert">{error}{!preview && !loading && <button className="rd-btn rd-btn-ghost" onClick={() => setRetry(n => n + 1)}>Retry</button>}</p>}
      <footer><button className="rd-btn rd-btn-ghost" disabled={busy} onClick={close}>{record ? "Done" : "Cancel"}</button>{!record && <button className="rd-btn rd-btn-primary" disabled={loading || busy || !preview?.allowed || !preview.parent || !summary.trim()} onClick={() => void send()}>{busy ? "Saving summary…" : keepOpen ? "Send summary" : "Send summary & close child"}</button>}</footer>
    </div>
  </Modal>, document.body);
}
