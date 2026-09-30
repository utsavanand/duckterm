import { useEffect, useRef, useState } from "react";
import { BugAttachment, BugContext, BugDraft, bugReport, captureFiles, reportBody } from "./bugReportData";
import { hostName } from "./hostTransport";
import "./bugReport.css";

export function BugReport({ session, onClose }: { session: string | null; onClose: () => void }) {
  const [context, setContext] = useState<BugContext | null>(null);
  const [removed, setRemoved] = useState(new Set<string>());
  const [summary, setSummary] = useState("");
  const [description, setDescription] = useState("");
  const [attachments, setAttachments] = useState<BugAttachment[]>([]);
  const [error, setError] = useState("");
  const [retry, setRetry] = useState(0);
  const [busy, setBusy] = useState(false);
  const [reading, setReading] = useState(false);
  const [prepared, setPrepared] = useState<{ draft: BugDraft; body: string; count: number } | null>(null);
  const [download, setDownload] = useState("");
  const [copyStatus, setCopyStatus] = useState("");
  const active = useRef(true);
  const sending = useRef(false);
  const root = useRef<HTMLElement>(null);
  useEffect(() => {
    active.current = true;
    const previous = document.activeElement as HTMLElement | null;
    root.current?.querySelector<HTMLInputElement>("input")?.focus();
    return () => { active.current = false; previous?.focus(); };
  }, []);
  useEffect(() => {
    let stale = false;
    void bugReport.context(session).then(value => { if (!stale) { setContext(value); setError(""); } }).catch((e: Error) => { if (!stale) setError(e.message); });
    return () => { stale = true; };
  }, [session, retry]);
  useEffect(() => () => { if (download) URL.revokeObjectURL(download); }, [download]);
  const body = reportBody(summary, description, context, removed);
  const bodyBytes = new TextEncoder().encode(body).length;
  const tooLong = bodyBytes > (context?.limits.body_bytes ?? 65536);
  async function prepare() {
    if (!context || sending.current || reading || !summary.trim() || !description.trim() || tooLong) return;
    sending.current = true; setBusy(true); setError("");
    try {
      const draft = await bugReport.prepare(session, summary, body, attachments);
      if (draft.status !== "draft_prepared" || draft.sent !== false) throw new Error("Unexpected report response. No mail draft was opened.");
      if (active.current) setPrepared({ draft, body, count: attachments.length });
    } catch (e) { if (active.current) setError((e as Error).message); }
    finally { sending.current = false; if (active.current) setBusy(false); }
  }
  async function loadBundle() {
    if (!prepared || busy) return;
    setBusy(true); setError("");
    try { const blob = await bugReport.bundle(session, prepared.draft.download_url); if (active.current) {
      const url = URL.createObjectURL(blob); setDownload(url);
      const link = document.createElement("a"); link.href = url; link.download = "duckterm-bug-report.zip";
      document.body.append(link); link.click(); link.remove();
    } }
    catch (e) { if (active.current) setError((e as Error).message); }
    finally { if (active.current) setBusy(false); }
  }
  async function attach(files: File[]) {
    if (!context || reading) return;
    setReading(true); setError("");
    try { const added = await captureFiles(files, attachments, context.limits); if (active.current) setAttachments(previous => [...previous, ...added]); }
    catch (e) { if (active.current) setError((e as Error).message); }
    finally { if (active.current) setReading(false); }
  }
  const mailto = prepared?.draft.mailto_url?.startsWith("mailto:") ? prepared.draft.mailto_url : null;
  return <section ref={root} className="rd-bug-report" role="dialog" aria-modal="true" aria-labelledby="bug-title" onKeyDown={event => {
    if (event.key === "Escape" && !busy && !reading) { event.stopPropagation(); onClose(); }
    if (event.key === "Tab") {
      const controls = [...(root.current?.querySelectorAll<HTMLElement>('button:not(:disabled), input:not(:disabled), textarea:not(:disabled), a[href]') ?? [])].filter(el => el.offsetParent !== null);
      const next = event.shiftKey ? controls.at(-1) : controls[0];
      if ((event.shiftKey && document.activeElement === controls[0]) || (!event.shiftKey && document.activeElement === controls.at(-1))) { event.preventDefault(); next?.focus(); }
    }
  }}><div className="rd-bug-inner">
    <header><div><small>{session ? hostName(session) : "This Mac"}{session ? " · Selected session" : ""}</small><h1 id="bug-title">Report a bug</h1><p>Describe the problem, choose what to include, then prepare a mail draft.</p></div><button className="rd-btn rd-btn-ghost" disabled={busy || reading} onClick={onClose}>Close</button></header>
    {error && <div className="rd-bug-error" role="alert">{error}{!context && <button className="rd-btn rd-btn-ghost" onClick={() => setRetry(n => n + 1)}>Retry context</button>}</div>}
    {prepared ? <div className="rd-bug-result"><span className="rd-bug-check" aria-hidden="true">✓</span><h2>Draft prepared</h2><p>Nothing has been sent. Your report bundle is ready to download from {session ? hostName(session) : "this server"}.</p>
      {!prepared.draft.recipient && <p role="status">This draft has no recipient. Add the support address in your mail app before sending.</p>}
      <section><h3>1. Open the mail draft</h3>{mailto ? <><p>Review the report in your mail app before sending.</p><a className="rd-btn rd-btn-primary" href={mailto}>Open mail draft</a></> : <p>This report is too long for a mail link. Download the ZIP and open draft.eml for the complete report.</p>}</section>
      <section><h3>2. Download the report bundle</h3><p>Contains report.md, draft.eml, and {prepared.count} selected {prepared.count === 1 ? "attachment" : "attachments"}.</p>{download ? <a className="rd-btn rd-btn-ghost" href={download} download="duckterm-bug-report.zip">Save ZIP</a> : <button className="rd-btn rd-btn-ghost" disabled={busy} onClick={() => void loadBundle()}>{busy ? "Preparing download…" : "Download ZIP"}</button>}<p>Extract the ZIP and attach any included files manually. If no mail app opens, use draft.eml or copy the report text.</p></section>
      <footer><button className="rd-btn rd-btn-ghost" onClick={() => { void navigator.clipboard.writeText(prepared.body).then(() => setCopyStatus("Report copied"), () => setError("Could not copy. Download report.md from the ZIP instead.")); }}>Copy report text</button><button className="rd-btn rd-btn-ghost" onClick={() => { setPrepared(null); setDownload(""); setCopyStatus(""); }}>Edit report</button><span role="status">{copyStatus}</span></footer>
    </div> : <div className="rd-bug-grid"><div className="rd-bug-fields">
      <label>Summary<input value={summary} maxLength={200} disabled={busy} onChange={e => setSummary(e.target.value.replace(/[\r\n\t]/g, " "))} placeholder="What went wrong?" /></label>
      <label>What happened?<textarea value={description} disabled={busy} rows={6} onChange={e => setDescription(e.target.value)} placeholder="What you did, what you expected, and what happened instead." /></label>
      <div className="rd-bug-context-heading"><h2>Included context</h2><button className="rd-btn rd-btn-ghost rd-btn-sm" disabled={busy || !context} onClick={() => setRemoved(new Set(context?.items.map(item => item.id)))}>Remove all</button></div>
      {!context && !error && <p role="status">Loading removable context…</p>}
      <div className="rd-bug-context">{context?.items.map(item => <label key={item.id} className={removed.has(item.id) ? "excluded" : ""}><input type="checkbox" checked={!removed.has(item.id)} disabled={busy} onChange={e => setRemoved(previous => { const next = new Set(previous); if (e.target.checked) next.delete(item.id); else next.add(item.id); return next; })} /><span>{item.label}<small>{item.text}</small></span></label>)}</div>
      <p className="rd-bug-help">Context describes the selected server. Terminal output, conversations, raw logs, and other sessions’ content are not collected.</p>
      <label>Attachments<input type="file" multiple disabled={busy || reading || !context} onChange={e => { const files = [...(e.target.files ?? [])]; e.target.value = ""; void attach(files); }} /></label><p className="rd-bug-help">Up to 5 files, 5 MiB each, 15 MiB total. Choose only files you want included.</p>
      {reading && <p role="status">Reading selected files…</p>}
      <ul className="rd-bug-files">{attachments.map((file, index) => <li key={index}>{/^image\/(png|jpeg|webp|gif)$/.test(file.type) && <img src={`data:${file.type};base64,${file.content_base64}`} alt={`Selected attachment ${file.name}`} />}<span>{file.name}<small>{(file.size / 1024).toFixed(1)} KiB</small></span><button className="rd-btn rd-btn-ghost rd-btn-sm" disabled={busy || reading} aria-label={`Remove ${file.name}`} onClick={() => setAttachments(previous => previous.filter((_, i) => i !== index))}>Remove</button></li>)}</ul>
    </div><aside className="rd-bug-preview"><h2>Review the complete report</h2><p>This exact text will be used for the draft. Removed context stays out.</p><div className="rd-bug-recipient">To {context?.recipient || "Unaddressed — add a support address in your mail app"} · Email draft</div><pre aria-label="Complete report">{body}</pre><footer><button className="rd-btn rd-btn-primary" disabled={!context || busy || reading || !summary.trim() || !description.trim() || tooLong} onClick={() => void prepare()}>{busy ? "Preparing draft…" : "Prepare mail draft"}</button><p className={tooLong ? "rd-bug-error" : ""}>{bodyBytes.toLocaleString()} / {(context?.limits.body_bytes ?? 65536).toLocaleString()} bytes</p><p>Creates a draft and a download bundle. You review and send from your mail app.</p></footer></aside></div>}
  </div></section>;
}
