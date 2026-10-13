import { useEffect, useState } from "react";
import { CheckpointAge, publishCheckpointClock } from "./CheckpointAge";
import { memoryGet, type MemoryEvidence, type MemoryRead, type SavedMemory } from "./memoryViewApi";
import { CheckpointProgress } from "./CheckpointProgress";
import { checkpointRequestError, useCheckpointAttempt } from "./checkpointRequests";
import { chooseCheckpoint } from "./checkpointSelection";
import { useToast } from "./ui";
import { checkpointFailure } from "./checkpointState";
import "./memoryView.css";

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
export function MemoryView({ sessionKey, name, active = true, initialRevision = "", onBack }: { sessionKey: string; name: string; active?: boolean; initialRevision?: string; onBack?: () => void }) {
  const [revision, setRevision] = useState(initialRevision);
  const [choices, setChoices] = useState<SavedMemory["revisions"]>([]);
  const [saved, setSaved] = useState<SavedMemory | null>(null);
  const [error, setError] = useState("");
  const [generation, refresh] = useState(0);
  const attempt = useCheckpointAttempt(sessionKey);
  const toast = useToast();
  useEffect(() => {
    if (!active) return;
    let live = true;
    setSaved(null); setError("");
    void memoryGet<SavedMemory>(sessionKey, "", revision ? { revision } : {}).then(value => {
      if (!value || !Array.isArray(value.revisions) || !["available", "unavailable", "empty"].includes(value.state)) throw new Error("Invalid memory response");
      if (revision && value.revision && value.revision.id !== revision) throw new Error("The requested saved summary was not returned. Try reading it again.");
      if (live) { setSaved(value); setChoices(value.revisions); publishCheckpointClock(sessionKey, value.checkpoint_clock); }
    }).catch(cause => { if (live) setError((cause as Error).message); });
    return () => { live = false; };
  }, [sessionKey, revision, generation, active]);
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
    <header><div><span className="hint">{name} · Memory</span><h1>Saved summary and evidence</h1><CheckpointAge sessionKey={sessionKey} /></div>
      <label>Summary revision<select aria-label="Summary revision" value={revision} onChange={event => setRevision(event.target.value)}>
        <option value="">Current memory</option>
        {revision && !choices.some(row => row.id === revision) && <option value={revision}>{current?.id === revision ? `${new Date(current.created_at).toLocaleString()} · ${revision.slice(0, 8)}` : `Selected summary · ${revision.slice(0, 8)}`}</option>}
        {choices.map(row => <option key={row.id} value={row.id}>{new Date(row.created_at).toLocaleString()} · {row.id.slice(0, 8)}</option>)}
      </select></label>
    </header>
    <div className="rd-memory-actions"><button className="rd-btn rd-btn-primary" disabled={attempt?.state === "running" || (attempt?.state === "unknown" && attempt.recovering)} onClick={() => { void chooseCheckpoint(sessionKey, name).catch(cause => toast(checkpointRequestError(cause), "err")); }}>Checkpoint</button>
      <span className="hint">History is recorded automatically. Checkpoint updates the summary when you choose.</span></div>
    <CheckpointProgress sessionKey={sessionKey} />
    {!attempt && (latestUpdate?.state === "failed" || latestUpdate?.state === "partial") && <div className="rd-memory-failure" role="status">
      <strong>{latestUpdate.state === "partial" ? "Checkpoint failed · summary incomplete" : "Latest summary update failed"}</strong>
      <p>{latestUpdate.state === "partial" ? "The latest attempt did not summarize its required range." : checkpointFailure({ summary_update: latestUpdate }) + "."} The last-success clock has not reset. {current?.summary ? "Previously published memory is shown below." : "No usable saved summary is displayed."}</p>
    </div>}
    {error ? <p role="alert">{error} <button className="rd-btn rd-btn-sm" onClick={() => refresh(n => n + 1)}>Retry memory read</button></p> : !saved ? <p role="status">Reading saved memory…</p> : saved.state === "empty" ? <p>No saved summary yet. Recorded conversations are separate; nothing has been deleted.</p> : <>
      {saved.state === "unavailable" && <p role="alert">Saved evidence is unavailable. Affected summary text and claims are withheld.</p>}
      {current && <>
        <p className="hint">{revision ? "Individual historical summary · claim states at save" : "Latest published summary"} · {new Date(current.created_at).toLocaleString()} · {current.id.slice(0, 8)}</p>
        {current.summary && <section><h2>Saved summary</h2><p className="rd-memory-summary">{current.summary}</p></section>}
        {aggregate?.state === "unavailable" && <p role="alert">Combined memory is unavailable ({aggregate.reason === "budget_exhausted" ? "saved claims exceed the combined-view budget" : "evidence could not be verified"}). Select an individual summary to inspect its retained content.</p>}
        <section><h2>Saved knowledge</h2><p className="hint">{aggregate?.span_count ? `${aggregate.span_count} independent spans. ` : ""}These are saved claims with linked evidence. Reading this view does not generate a summary or certify a new harness switch.</p>
          <div className="rd-memory-kind-counts">{["decision", "constraint", "learning", "callout", "question", "unfinished", "goal", "risk", "unclassified"].map(kind => <span key={kind}>{kind}: {groups.flatMap(g => g.claims).filter(c => (c.kind ?? "unclassified") === kind).length}</span>)}</div>
        </section>
        {aggregate?.narratives?.length ? <section><h2>Recent work</h2>{aggregate.narratives.map(row => <p key={row.revision}>{row.text}</p>)}{Boolean(aggregate.older_narratives_for_retrieval) && <p className="hint">Earlier narrative summaries remain available through memory read tools.</p>}</section> : current.overview && <section><h2>Saved working context</h2><p>{current.overview}</p></section>}
        {groups.map(group => <section key={group.field}><h2>{labels[group.field] ?? group.field}</h2>{group.claims.length ? group.claims.map(claim => <div className="rd-memory-claim" key={`${claim.source ?? current.source}/${claim.id ?? claim.record}`}>
          <p>{claim.text}</p><span className="hint">{claim.kind ?? "unclassified"} · {claim.state ?? "open"}</span>
          {claim.evidence.map(evidence => <Evidence key={`${sessionKey}/${evidence.source}/${evidence.record}`} sessionKey={sessionKey} evidence={evidence} />)}
        </div>) : <p className="hint">No saved claim in this group.</p>}</section>)}
        {Boolean(current.withheld_claims) && <p role="alert">Some claims are withheld because their evidence is unavailable.</p>}
      </>}
      {saved.revisions_truncated && <p className="hint">Showing the latest 20 revisions. Older revisions remain accessible through memory read tools.</p>}
    </>}
  </section>;
}
