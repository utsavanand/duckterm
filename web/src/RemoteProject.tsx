import { useEffect, useRef, useState } from "react";
import { destinationRequest, desktop, selectLaunchTarget } from "./desktop";
import { Button, Field, inputStyle, Modal } from "./ui";
import { SessionView } from "./types";

export type PreparedProject = { id: string; destination: string; stage: string; session_key?: string; offset?: number; bytes?: number };
type Review = { requirements?: string[]; linux_compatible?: boolean; setup?: string[]; fingerprint: string; source: string; bytes: number; entries: { path: string }[]; excluded: string[]; ignored: string[]; git: { kind: string }; conversation: { runtime?: string; id?: string; sha256?: string } };
type Draft = { id: string; source: string; destination: string; url: string; branch: string; selected: string[] };

export function RemoteProject({ target, kind, command, sourceSession, sourcePath, onPrepared, onBusy }: {
  target: string; kind: "copy" | "clone"; command: string; sourceSession?: string; sourcePath?: string;
  onPrepared: (project: PreparedProject) => void | Promise<void>; onBusy: (busy: boolean) => void;
}) {
  const storage = `remote-project:${target}:${sourceSession ?? "new"}:${kind}`;
  const [draft, setDraft] = useState<Draft>(() => {
    try { const saved = localStorage.getItem(storage); if (saved) return JSON.parse(saved) as Draft; } catch { /* New draft if storage is unavailable. */ }
    return { id: crypto.randomUUID().replaceAll("-", ""), source: sourcePath ?? "", destination: "", url: "", branch: "", selected: [] };
  });
  const [review, setReview] = useState<Review | null>(null);
  const [checked, setChecked] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [progress, setProgress] = useState("");
  const [reviewed, setReviewed] = useState(false);
  const mounted = useRef(true);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  useEffect(() => { localStorage.setItem(storage, JSON.stringify(draft)); }, [draft, storage]);
  function change(key: keyof Draft, value: string | string[]) {
    setDraft(d => ({ ...d, [key]: value, id: crypto.randomUUID().replaceAll("-", "") }));
    setReview(null); setChecked(false); setReviewed(false); setError("");
  }
  async function inspect() {
    setBusy(true); onBusy(true); setError("");
    try {
      const snapshot = kind === "copy" ? await destinationRequest<Review>("local", "project-preview", { source: draft.source, source_session: sourceSession, selected: draft.selected }) : null;
      const preflight = await destinationRequest<{ destination: string }>(target, "project-preflight", { destination: draft.destination, command, runtime: snapshot?.conversation.runtime, requirements: snapshot?.requirements, linux_compatible: snapshot?.linux_compatible });
      if (!mounted.current) return;
      if (preflight.destination) setDraft(d => ({ ...d, destination: preflight.destination }));
      setReview(snapshot); setReviewed(true); setChecked(false);
    } catch (e) { if (mounted.current) setError((e as Error).message); }
    finally { if (mounted.current) setBusy(false); onBusy(false); }
  }
  async function transfer() {
    setBusy(true); onBusy(true); setError(""); setProgress("Preparing project…");
    const poll = window.setInterval(() => {
      destinationRequest<PreparedProject>(target, "project-status", { id: draft.id }).then(s => {
        if (mounted.current && s.stage) setProgress(s.stage === "receiving" ? `Copying ${Math.round(100 * (s.offset ?? 0) / (s.bytes || 1))}%` : s.stage);
      }).catch(() => undefined);
    }, 1000);
    try {
      const result = await destinationRequest<PreparedProject>(target, kind === "copy" ? "project-transfer" : "project-clone", {
        ...draft, source_session: sourceSession, fingerprint: review?.fingerprint, conversation_sha256: review?.conversation.sha256,
      });
      if (!mounted.current) return;
      setProgress("Project verified and ready");
      await onPrepared(result);
    } catch (e) { if (mounted.current) setError((e as Error).message); }
    finally { clearInterval(poll); if (mounted.current) setBusy(false); onBusy(false); }
  }
  return <div>
    {kind === "copy" ? <Field label="Source project on This Mac">
      <input aria-label="Local project path" style={inputStyle} value={draft.source} disabled={busy || !!sourceSession} onChange={e => change("source", e.target.value)} placeholder="/Users/you/projects/my-project" />
    </Field> : <>
      <Field label="Repository URL"><input aria-label="Repository URL" style={inputStyle} value={draft.url} disabled={busy} onChange={e => change("url", e.target.value)} placeholder="https://github.com/owner/project.git" /></Field>
      <Field label="Branch (optional)"><input aria-label="Clone branch" style={inputStyle} value={draft.branch} disabled={busy} onChange={e => change("branch", e.target.value)} /></Field>
      <p>Uses Git authorization on the remote computer. Local edits are not included.</p>
    </>}
    <Field label={`New folder on ${desktop()?.targets.find(t => t.id === target)?.name ?? target}`}><input aria-label="Remote destination folder" style={inputStyle} value={draft.destination} disabled={busy} onChange={e => change("destination", e.target.value)} placeholder="/home/duckterm/projects/new-project" /></Field>
    <Button onClick={inspect} disabled={busy || !draft.destination || !(kind === "copy" ? draft.source : draft.url)}>Review transfer</Button>
    {reviewed && <div style={{ marginTop: 12 }}>
      <p>Destination runtime is available. No dependency installation commands will run automatically.</p>
      {review && <>
        {review.setup?.map(step => <p key={step}>{step}</p>)}
        <p>{review.entries.length} files · {(review.bytes / 1048576).toFixed(1)} MiB{review.git.kind === "git" ? " · Git history, index, and working changes included" : ""}</p>
        <details><summary>Files to copy</summary><pre style={{ maxHeight: 150, overflow: "auto" }}>{review.entries.map(e => e.path).join("\n")}</pre></details>
        <details><summary>Excluded files ({review.excluded.length})</summary><pre style={{ maxHeight: 150, overflow: "auto" }}>{review.excluded.join("\n")}</pre></details>
        {review.ignored.length > 0 && <details><summary>Include ignored files deliberately</summary>{review.ignored.map(path => <label key={path} style={{ display: "block" }}><input type="checkbox" checked={draft.selected.includes(path)} disabled={busy} onChange={e => change("selected", e.target.checked ? [...draft.selected, path] : draft.selected.filter(p => p !== path))} />{path}</label>)}</details>}
        {sourceSession && <p>Resume {review.conversation.runtime} conversation {review.conversation.id}. Its transcript may contain sensitive task content. Existing remote authorization is used.</p>}
      </>}
      <p>Review Linux compatibility and required dependencies. Credentials, caches, and build output are excluded by default; Git history may contain historical sensitive content.</p>
      <label><input type="checkbox" checked={checked} disabled={busy} onChange={e => setChecked(e.target.checked)} />I reviewed these files and the destination requirements.</label>
      <div style={{ marginTop: 12 }}><Button disabled={busy || !checked} onClick={transfer}>{sourceSession ? "Move to remote" : kind === "copy" ? "Copy project" : "Clone repository"}</Button></div>
    </div>}
    {progress && <p role="status">{progress}</p>}
    {error && <p role="alert">{error}</p>}
    {busy && kind === "copy" && <Button variant="ghost" onClick={() => destinationRequest(target, "project-pause", { id: draft.id }).catch(() => undefined)}>Pause transfer</Button>}
    <small>Retry uses this operation's saved snapshot. Closing and reopening restores the draft. Your local files remain in place.</small>
  </div>;
}

export function MoveRemoteModal({ session, onClose }: { session: SessionView; onClose: () => void }) {
  const hosts = desktop()?.targets.filter(t => t.id !== "local") ?? [];
  const [target, setTarget] = useState(hosts[0]?.id ?? "");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<PreparedProject | null>(null);
  async function moved(project: PreparedProject) {
    const next = await destinationRequest<PreparedProject>(target, "project-launch", { id: project.id, source_session: session.key, name: session.label });
    setResult(next);
    localStorage.setItem(`moved-session:${session.key}`, JSON.stringify({ target, key: next.session_key }));
  }
  return <Modal title={`Move ${session.label} to remote`} onClose={() => { if (!busy) onClose(); }}>
    {result ? <><p>Moved to {hosts.find(t => t.id === target)?.name}. The stopped local session and project remain available for recovery.</p><Button onClick={() => { selectLaunchTarget(target, {}); onClose(); }}>Open remote session</Button></> : <>
      <Field label="Remote computer"><select aria-label="Remote computer" style={inputStyle} value={target} disabled={busy} onChange={e => setTarget(e.target.value)}>{hosts.map(h => <option key={h.id} value={h.id}>{h.name}</option>)}</select></Field>
      {!hosts.length ? <p>Add a computer under Settings → Remote computers first.</p> : <RemoteProject key={target} target={target} kind="copy" command={session.runtime === "codex" ? "codex" : "claude"} sourceSession={session.key} sourcePath={session.worktreePath ?? session.cwd} onPrepared={moved} onBusy={setBusy} />}
    </>}
    <Button variant="ghost" disabled={busy} onClick={onClose}>Close</Button>
  </Modal>;
}
