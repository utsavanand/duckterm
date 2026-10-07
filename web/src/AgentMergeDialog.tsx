import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { Modal } from "./ui";
import "./forkMerge.css";
import "./agentMerge.css";

export interface MergeTarget { key: string; name: string; group: string; state: string; allowed: boolean; reason?: string; priorityDelivery: boolean }
interface MergeGit { allowed: boolean; reason?: string; sourcePath?: string; targetPath?: string; sourceCommit?: string; targetCommit?: string; sourceBranch?: string; targetBranch?: string; commits?: number; changes?: string }
export interface AgentMergePreview { source: { key: string; name: string }; target: MergeTarget; context: string; notes: string; git: MergeGit; revision: string }
export interface AgentMergeDraft { target: string; revision: string; context: string | null; notes: string | null; code: boolean; requestKey: string }
export interface AgentMergeRecord { id: string; source: string; sourceName: string; target: string; targetName: string; context: string | null; notes: string | null; git: MergeGit | null; packet: string; createdAt: number; delivery: string; statusAvailable: boolean; reply?: string | null }
export interface AgentMergeService {
  targets: () => Promise<{ destinations: MergeTarget[] }>;
  preview: (target: string) => Promise<AgentMergePreview>;
  send: (draft: AgentMergeDraft) => Promise<AgentMergeRecord>;
  history: () => Promise<{ merges: AgentMergeRecord[] }>;
}

export function AgentMergeDialog({ service, sourceName, onClose }: { service: AgentMergeService; sourceName: string; onClose: () => void }) {
  const [targets, setTargets] = useState<MergeTarget[]>([]), [target, setTarget] = useState("");
  const [preview, setPreview] = useState<AgentMergePreview | null>(null);
  const [context, setContext] = useState(""), [notes, setNotes] = useState("");
  const [includeContext, setIncludeContext] = useState(true), [includeNotes, setIncludeNotes] = useState(true), [code, setCode] = useState(false);
  const [loading, setLoading] = useState(true), [busy, setBusy] = useState(false), [reviewing, setReviewing] = useState(false);
  const [error, setError] = useState(""), [historyError, setHistoryError] = useState("");
  const [retry, setRetry] = useState(0), [record, setRecord] = useState<AgentMergeRecord | null>(null), [history, setHistory] = useState<AgentMergeRecord[]>([]);
  const initialized = useRef(false), sending = useRef(false), alive = useRef(true);
  const submission = useRef<{ signature: string; requestKey: string }>();
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);
  useEffect(() => {
    let live = true;
    setLoading(true); setError("");
    void service.targets().then(value => { if (live) { setTargets(value.destinations); setLoading(false); } }).catch(e => { if (live) { setError(`Could not load agents: ${e.message}. If this is a remote session, update DuckTerm on both computers.`); setLoading(false); } });
    void service.history().then(value => { if (live) { setHistory(value.merges); setHistoryError(""); } }).catch(e => { if (live) setHistoryError(`Could not load recent merges: ${e.message}`); });
    return () => { live = false; };
  }, [service, retry]);
  useEffect(() => {
    let live = true;
    setPreview(null); setCode(false); setReviewing(false); setError("");
    if (!target) return;
    setLoading(true);
    void service.preview(target).then(value => {
      if (!live) return;
      setPreview(value);
      if (!initialized.current) { setContext(value.context); setNotes(value.notes); setIncludeContext(!!value.context.trim()); setIncludeNotes(!!value.notes.trim()); initialized.current = true; }
    }).catch(e => { if (live) setError(`Could not prepare merge: ${e.message}`); }).finally(() => { if (live) setLoading(false); });
    return () => { live = false; };
  }, [service, target, retry]);
  const recordId = record?.id;
  useEffect(() => {
    if (!recordId) return;
    let live = true, running = false;
    let timer: ReturnType<typeof setTimeout>;
    const visible = () => document.visibilityState !== "hidden";
    async function refresh() {
      if (!live || running || !visible()) return;
      running = true;
      try {
        const value = await service.history();
        if (live) {
          const updated = value.merges.find(item => item.id === recordId);
          if (updated) { setRecord(updated); setHistoryError(""); }
          else setHistoryError("This request is no longer in recent history. Showing its last recorded state.");
        }
      } catch (e) { if (live) setHistoryError(`Could not refresh delivery: ${(e as Error).message}`); }
      finally { running = false; if (live && visible()) timer = setTimeout(() => void refresh(), 4000); }
    }
    const visibility = () => { clearTimeout(timer); void refresh(); };
    document.addEventListener("visibilitychange", visibility); void refresh();
    return () => { live = false; clearTimeout(timer); document.removeEventListener("visibilitychange", visibility); };
  }, [recordId, service]);
  const close = () => { if (!sending.current) onClose(); };
  const valid = !!preview?.target.allowed && (includeContext || includeNotes || code) && (!includeContext || !!context.trim()) && (!includeNotes || !!notes.trim()) && (!code || preview.git.allowed);
  async function send() {
    if (sending.current || !preview || !valid) return;
    const draft = { target, revision: preview.revision, context: includeContext ? context : null, notes: includeNotes ? notes : null, code };
    const signature = JSON.stringify(draft);
    if (submission.current?.signature !== signature) submission.current = { signature, requestKey: crypto.randomUUID() };
    sending.current = true; setBusy(true); setError("");
    try { const result = await service.send({ ...draft, requestKey: submission.current.requestKey }); if (alive.current) setRecord(result); }
    catch (e) { if (alive.current) setError(`Could not confirm the merge: ${(e as Error).message}. Your draft is kept. Retry unchanged to check the same request; it may already be saved.`); }
    finally { sending.current = false; if (alive.current) setBusy(false); }
  }
  return createPortal(<div onKeyDown={event => {
    if (event.key === "Escape") { event.preventDefault(); event.stopPropagation(); close(); }
    if (event.key === "Tab") {
      const controls = [...event.currentTarget.querySelectorAll<HTMLElement>("button:not(:disabled),select:not(:disabled),input:not(:disabled),textarea:not(:disabled)")];
      const first = controls[0], last = controls[controls.length - 1];
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
    }
  }}><Modal title={record ? "Merge request saved" : reviewing ? "Review merge" : "Merge with agent"} onClose={close} width={660}>
    <div className="rd-merge-review rd-agent-merge" role="dialog" aria-modal="true" aria-label="Merge with agent" aria-busy={busy || loading}>
      {!record && !reviewing && <>
        <p>Bring selected context into another agent. Both sessions stay open.</p>
        <label className="rd-merge-label" htmlFor="merge-agent-target">Destination agent</label>
        <select autoFocus id="merge-agent-target" value={target} disabled={busy} onChange={e => setTarget(e.target.value)}><option value="">Choose an agent</option>{targets.map(item => <option key={item.key} value={item.key} disabled={!item.allowed}>{item.name} · {item.group}{item.allowed ? ` · ${item.state}` : ` — ${item.reason}`}</option>)}</select>
        <p>Agents on this computer only. Cross-computer merges are not available yet.</p>
        {loading && <p role="status">Preparing merge options…</p>}
        {!loading && !targets.some(item => item.allowed) && <p>No available destination. Add another agent to a shared folder to give it an inbox.</p>}
        {preview && <>
          <div className="rd-merge-route"><strong>{sourceName}</strong>→<strong>{preview.target.name}</strong></div>
          {!preview.target.allowed && <p role="alert">{preview.target.reason}</p>}
          <label className="rd-agent-merge-choice"><input type="checkbox" checked={includeContext} onChange={e => setIncludeContext(e.target.checked)} />Conversation context<small>A reviewed summary. The original conversations remain separate.</small></label>
          {includeContext && <textarea aria-label="Context to send" value={context} maxLength={12000} placeholder="Review or write the context the destination should receive" onChange={e => setContext(e.target.value)} />}
          {!context && <p>No saved conversation summary is available. Select Conversation context to write one.</p>}
          <label className="rd-agent-merge-choice"><input type="checkbox" checked={includeNotes} onChange={e => setIncludeNotes(e.target.checked)} />Notes<small>Append these notes once, preserving existing destination notes.</small></label>
          {includeNotes && <textarea aria-label="Notes to append" value={notes} maxLength={12000} onChange={e => setNotes(e.target.value)} />}
          <label className="rd-agent-merge-choice"><input type="checkbox" checked={code} disabled={!preview.git.allowed} onChange={e => setCode(e.target.checked)} />Code from the other worktree<small>Ask the destination agent to integrate the reviewed commits and report conflicts.</small></label>
          {!preview.git.allowed && <p>{preview.git.reason}</p>}
          {code && <GitReview git={preview.git} />}
        </>}
      </>}
      {!record && reviewing && preview && <>
        <div className="rd-merge-route"><strong>{sourceName}</strong>→<strong>{preview.target.name}</strong></div>
        {includeContext && <section><h3>Conversation context</h3><pre>{context}</pre></section>}
        {includeNotes && <section><h3>Notes to append</h3><pre>{notes}</pre></section>}
        {code && <GitReview git={preview.git} />}
        <p>{preview.target.priorityDelivery ? "The request reaches an idle agent immediately, or waits until a busy agent finishes its turn." : "This agent receives the request in its inbox; automatic delivery is unavailable."}</p>
        <p>Both agents stay open. {includeNotes && "The reviewed notes are appended once when you send."}</p>
      </>}
      {record && <section aria-label="Saved merge request"><strong>{record.sourceName} → {record.targetName}</strong><p>Delivery: {record.delivery}</p><p>{record.notes !== null ? "Notes appended once. " : ""}Both agents remain open.</p>{record.git && <p>Code integration was requested. Delivery or acknowledgement does not confirm that the code is merged.</p>}<pre>{record.packet}</pre>{record.reply && <><h3>Destination reply</h3><pre>{record.reply}</pre></>}{!record.statusAvailable && <p>Live delivery tracking is unavailable.</p>}</section>}
      {!record && !reviewing && history.length > 0 && <details><summary>Recent merge requests ({history.length})</summary>{history.map(item => <button type="button" className="rd-agent-merge-history" key={item.id} onClick={() => setRecord(item)}>{item.sourceName} → {item.targetName} · {item.delivery} · {new Date(item.createdAt).toLocaleString()}</button>)}</details>}
      {historyError && <p role="status">{historyError}</p>}
      {error && <p className="rd-merge-error" role="alert">{error}</p>}
      <footer><button className="rd-btn" disabled={busy} onClick={close}>{record ? "Done" : "Cancel"}</button>{!record && <>{reviewing ? <button className="rd-btn" disabled={busy} onClick={() => setReviewing(false)}>Back</button> : <button className="rd-btn" disabled={busy || loading} onClick={() => setRetry(n => n + 1)}>Refresh preview</button>}<button className="rd-btn rd-btn-primary" disabled={busy || loading || !valid} onClick={() => reviewing ? void send() : setReviewing(true)}>{busy ? "Saving merge request…" : reviewing ? "Send merge request" : "Review merge"}</button></>}</footer>
    </div>
  </Modal></div>, document.body);
}

function GitReview({ git }: { git: MergeGit }) {
  return <section className="rd-merge-worktree"><h3>Code integration request</h3><p>{git.sourceBranch} → {git.targetBranch} · {git.commits} {git.commits === 1 ? "commit" : "commits"}</p><code>{git.sourceCommit} → {git.targetCommit}</code><p>{git.sourcePath}<br />→ {git.targetPath}</p><pre>{git.changes}</pre><p>Only committed changes are included. The destination agent rechecks both worktrees and reports conflicts. No resets, overwritten edits, removed worktrees or automatic publishing. Delivery does not mean code has been merged.</p></section>;
}
