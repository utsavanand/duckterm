import { useEffect, useState } from "react";
import { CheckpointAge, publishCheckpointClock } from "./CheckpointAge";
import { memoryGet, type MemoryEvidence, type MemoryRead, type SavedMemory } from "./memoryViewApi";
import { CheckpointMemoryView } from "./CheckpointMemoryView";
import { MemoryPages, validMemoryPage } from "./MemoryPages";
import { CheckpointProgress } from "./CheckpointProgress";
import { checkpointRequestError, useCheckpointAttempt } from "./checkpointRequests";
import { chooseCheckpoint } from "./checkpointSelection";
import { useToast } from "./ui";
import { checkpointFailure } from "./checkpointState";
import "./memoryView.css";
import { checkpointMemoryAfterFailure } from "./CheckpointFailureCoverage";

const labels: Record<string, string> = { goals: "Goals", constraints: "Constraints, callouts and learnings", decisions: "Decisions and learnings", unfinished: "Unfinished work", questions: "Open questions", risks: "Risks" };
function Evidence({ sessionKey, evidence }: { sessionKey: string; evidence: MemoryEvidence }) {
  const [read, setRead] = useState<MemoryRead | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  async function load(more = false) {
    if (busy) return;
    setBusy(true); setError("");
    try {
      const value = await memoryGet<MemoryRead>(sessionKey, "read", { source: evidence.source, record: evidence.record, ...(more && read?.next_offset !== null ? { offset: String(read?.next_offset ?? 0) } : {}) });
      if (typeof value.text !== "string") throw new Error("Invalid evidence response");
      setRead(previous => ({ ...value, text: more && previous ? previous.text + value.text : value.text }));
    } catch (cause) { setRead(null); setError((cause as Error).message); }
    finally { setBusy(false); }
  }
  return <details className="rd-memory-source" onToggle={event => { if (event.currentTarget.open && !read && !busy && !error) void load(); }}>
    <summary>{evidence.title} · {evidence.kind}</summary>
    <p className="hint">{evidence.retained ? "Retained original" : "Original source"} · {evidence.role}</p>
    <code>{evidence.source} / {evidence.record}</code>
    {read && <pre tabIndex={0}>{read.text}</pre>}
    {busy && <p role="status">Reading original evidence…</p>}
    {error && <p role="alert">{error} <button className="rd-btn rd-btn-sm" onClick={() => void load()}>Retry</button></p>}
    {read?.next_offset != null && <button className="rd-btn rd-btn-sm" disabled={busy} onClick={() => void load(true)}>Read more</button>}
  </details>;
}
export function MemoryView({ sessionKey, name, active = true, initialRevision = "", initialCheckpoint = "", onBack, readOnly = false }: { sessionKey: string; name: string; active?: boolean; initialRevision?: string; initialCheckpoint?: string; onBack?: () => void; readOnly?: boolean }) {
  const [revision, setRevision] = useState(initialRevision);
  const [mode, setMode] = useState<"checkpoint" | "knowledge" | "history">(initialCheckpoint ? "checkpoint" : initialRevision ? "history" : "knowledge");
  const [checkpointId, setCheckpointId] = useState(initialCheckpoint);
  const [pages, setPages] = useState([""]);
  const [stale, setStale] = useState(false);
  const cursor = pages[pages.length - 1];
  function navigate(next: "checkpoint" | "knowledge" | "history") { setMode(next); setRevision(""); setPages([""]); }
  function openRevision(id: string) { setMode("history"); setRevision(id); setPages([""]); }
  const [choices, setChoices] = useState<SavedMemory["revisions"]>([]);
  const [saved, setSaved] = useState<SavedMemory | null>(null);
  const [error, setError] = useState("");
  const [generation, refresh] = useState(0);
  const attempt = useCheckpointAttempt(sessionKey);
  const toast = useToast();
  useEffect(() => {
    if (!active || mode === "checkpoint") return;
    let live = true;
    setSaved(null); setError(""); setStale(false);
    void memoryGet<SavedMemory>(sessionKey, "", revision ? { revision } : cursor ? { [mode === "history" ? "revisions_cursor" : "claims_cursor"]: cursor } : {}).then(value => {
      if (!value || !Array.isArray(value.revisions) || !["available", "unavailable", "empty"].includes(value.state)) throw new Error("Invalid memory response");
      if (revision && value.revision && value.revision.id !== revision) throw new Error("The requested saved summary was not returned. Try reading it again.");
      if (value.memory?.pagination && !validMemoryPage(value.memory.pagination) || value.revisions_pagination && !validMemoryPage(value.revisions_pagination)) throw new Error("Invalid saved-memory page");
      if (live) { setSaved(value); setChoices(value.revisions); if (value.latest_checkpoint) setCheckpointId(initialCheckpoint || value.latest_checkpoint!.id); publishCheckpointClock(sessionKey, value.checkpoint_clock); }
    }).catch(cause => { if (live) { setError((cause as Error).message); setStale((cause as { status?: number }).status === 409); } });
    return () => { live = false; };
  }, [sessionKey, revision, generation, active, mode, cursor, initialCheckpoint]);
  useEffect(() => {
    const changed = (event: Event) => { if ((event as CustomEvent<string>).detail === sessionKey) refresh(n => n + 1); };
    window.addEventListener("duckterm-checkpoint", changed);
    return () => window.removeEventListener("duckterm-checkpoint", changed);
  }, [sessionKey]);
  const current = saved?.revision;
  const aggregate = revision ? undefined : saved?.memory;
  const latestUpdate = saved?.latest_checkpoint?.summary_update;
  const groups = aggregate?.state === "available" ? aggregate.groups : aggregate ? [] : current?.groups ?? [];
  return <section className="rd-memory-view" aria-label="Session memory">
    {onBack && <button className="rd-btn rd-btn-sm rd-btn-ghost" onClick={onBack}>← Timeline</button>}
    <header><div><span className="hint">{name} · Memory</span><h1>{mode === "checkpoint" ? "Checkpoint result" : mode === "history" && !revision ? "Revision history" : "Saved summary and evidence"}</h1><CheckpointAge sessionKey={sessionKey} /></div>
      <label>Summary revision<select aria-label="Summary revision" value={revision} onChange={event => event.target.value ? openRevision(event.target.value) : navigate("knowledge")}>
        <option value="">Current memory</option>
        {revision && !choices.some(row => row.id === revision) && <option value={revision}>{current?.id === revision ? `${new Date(current.created_at).toLocaleString()} · ${revision.slice(0, 8)}` : `Selected summary · ${revision.slice(0, 8)}`}</option>}
        {choices.map(row => <option key={row.id} value={row.id}>{new Date(row.created_at).toLocaleString()} · {row.id.slice(0, 8)}</option>)}
      </select></label>
    </header>
    <nav className="rd-memory-tabs" aria-label="Memory views">
      <button aria-pressed={mode === "checkpoint"} onClick={() => navigate("checkpoint")}>Checkpoint result</button>
      <button aria-pressed={mode === "knowledge"} onClick={() => navigate("knowledge")}>Saved knowledge</button>
      <button aria-pressed={mode === "history"} onClick={() => navigate("history")}>Revision history</button>
    </nav>
    {!readOnly && <div className="rd-memory-actions"><button className="rd-btn rd-btn-primary" disabled={attempt?.state === "running" || (attempt?.state === "unknown" && attempt.recovering)} onClick={() => { void chooseCheckpoint(sessionKey, name).catch(cause => toast(checkpointRequestError(cause), "err")); }}>Checkpoint</button>
      <span className="hint">History is recorded automatically. Checkpoint updates the summary when you choose.</span></div>}
    {!readOnly && <CheckpointProgress sessionKey={sessionKey} />}
    {mode !== "checkpoint" && !attempt && (latestUpdate?.state === "failed" || latestUpdate?.state === "partial") && <div className="rd-memory-failure" role="status">
      <strong>{latestUpdate.state === "partial" ? "Checkpoint failed · summary incomplete" : "Latest summary update failed"}</strong>
      <p>{latestUpdate.state === "partial" ? "The latest attempt did not summarize its required range." : checkpointFailure({ summary_update: latestUpdate }) + "."} {checkpointMemoryAfterFailure({ summary_update: latestUpdate })} {current?.summary ? "Saved memory is shown below." : "No usable saved summary is displayed."}</p>
    </div>}
    {mode === "checkpoint" ? <CheckpointMemoryView key={`${sessionKey}/${checkpointId}`} sessionKey={sessionKey} checkpointId={checkpointId} active={active} onRevision={openRevision} /> : error ? <p role="alert">{stale ? "Saved memory changed. Reload the first page to read one consistent version." : error} <button className="rd-btn rd-btn-sm" onClick={() => { setPages([""]); refresh(n => n + 1); }}>{stale ? "Reload first page" : "Retry memory read"}</button></p> : !saved ? <p role="status">Reading saved memory…</p> : saved.state === "empty" ? <p>No saved summary yet. Recorded conversations are separate; nothing has been deleted.</p> : <>
      {saved.state === "unavailable" && <p role="alert">Saved evidence is unavailable. Affected summary text and claims are withheld.</p>}
      {mode === "history" && !revision ? <section aria-label="Revision history">
        <p className="hint">Browse saved summaries individually. An individual revision is not the whole checkpoint.</p>
        {saved.revisions_pagination && <MemoryPages page={saved.revisions_pagination} noun="revisions" previous={pages.length > 1} onPrevious={() => setPages(p => p.slice(0, -1))} onNext={next => setPages(p => [...p, next])} />}
        {choices.map(row => <section key={row.id}><p>{new Date(row.created_at).toLocaleString()} · {row.id.slice(0, 8)}</p><button className="rd-timeline-link" onClick={() => openRevision(row.id)}>Open summary {row.id.slice(0, 8)} ↗</button></section>)}
      </section> : current && <>
        {revision && <button className="rd-timeline-link" onClick={() => navigate("history")}>← Revision history</button>}
        <p className="hint">{revision ? "Individual historical summary · claim states at save" : "Latest published summary"} · {new Date(current.created_at).toLocaleString()} · {current.id.slice(0, 8)}</p>
        {current.summary && <section><h2>Saved summary</h2><p className="rd-memory-summary">{current.summary}</p></section>}
        {aggregate?.state === "unavailable" && <p role="alert">Combined memory is unavailable ({aggregate.reason === "budget_exhausted" ? "saved claims exceed the combined-view budget" : "evidence could not be verified"}). Select an individual summary to inspect its retained content.</p>}
        <section><h2>Saved knowledge</h2><p className="hint">{aggregate?.span_count ? `${aggregate.span_count} independent spans. ` : ""}These are saved claims with linked evidence. Reading this view does not generate a summary or certify a new harness switch.</p>
          <div className="rd-memory-kind-counts">{["decision", "constraint", "learning", "callout", "question", "unfinished", "goal", "risk", "unclassified"].map(kind => <span key={kind}>{kind}: {groups.flatMap(g => g.claims).filter(c => (c.kind ?? "unclassified") === kind).length}</span>)}</div>
        </section>
        {aggregate?.narratives?.length ? <section><h2>Recent work</h2>{aggregate.narratives.map(row => <p key={row.revision}>{row.text}</p>)}{Boolean(aggregate.older_narratives_for_retrieval) && <p className="hint">Earlier narrative summaries remain available through memory read tools.</p>}</section> : current.overview && <section><h2>Saved working context</h2><p>{current.overview}</p></section>}
        {aggregate?.pagination && <><p className="hint">Counts above describe this page. Open, retired and superseded are recorded states; reading this list does not declare a handoff ready.</p><MemoryPages page={aggregate.pagination} noun="saved items" previous={pages.length > 1} onPrevious={() => setPages(p => p.slice(0, -1))} onNext={next => setPages(p => [...p, next])} /></>}
        {groups.map(group => <section key={group.field}><h2>{labels[group.field] ?? group.field}</h2>{group.claims.length ? group.claims.map(claim => <div className="rd-memory-claim" key={`${claim.source ?? current.source}/${claim.id ?? claim.record}`}>
          <p>{claim.text}</p><span className="hint">{claim.kind ?? "unclassified"} · {claim.state ?? "open"}</span>
          {claim.evidence.map(evidence => <Evidence key={`${sessionKey}/${evidence.source}/${evidence.record}`} sessionKey={sessionKey} evidence={evidence} />)}
        </div>) : <p className="hint">No saved claim in this group.</p>}</section>)}
        {Boolean(current.withheld_claims) && <p role="alert">Some claims are withheld because their evidence is unavailable.</p>}
      </>}
      {saved.revisions_truncated && !saved.revisions_pagination && <p className="hint">Older revisions remain accessible through memory read tools.</p>}
    </>}
  </section>;
}
