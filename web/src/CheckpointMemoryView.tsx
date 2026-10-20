import { useEffect, useState } from "react";
import { memoryGet, type CheckpointMemory } from "./memoryViewApi";
import { MemoryPages, validMemoryPage } from "./MemoryPages";
import { checkpointFailure } from "./checkpointState";

export function CheckpointMemoryView({ sessionKey, checkpointId, active, onRevision }: { sessionKey: string; checkpointId: string; active: boolean; onRevision: (id: string) => void }) {
  const [pages, setPages] = useState([""]);
  const [generation, refresh] = useState(0);
  const [value, setValue] = useState<CheckpointMemory["checkpoint"] | null>(null);
  const [error, setError] = useState("");
  const [stale, setStale] = useState(false);
  const cursor = pages[pages.length - 1];
  useEffect(() => {
    if (!active || !checkpointId) return;
    let live = true;
    setValue(null); setError(""); setStale(false);
    void memoryGet<CheckpointMemory>(sessionKey, "", { checkpoint: checkpointId, ...(cursor ? { revisions_cursor: cursor } : {}) }).then(result => {
      const cp = result?.checkpoint;
      if (result?.scope !== "checkpoint" || result.session_key !== sessionKey || cp?.id !== checkpointId
        || !Array.isArray(cp.batches) || !validMemoryPage(cp.pagination) || cp.batches.length !== cp.pagination.returned
        || cp.batches.some(batch => typeof batch.revision_id !== "string" || !["available", "unavailable", "empty"].includes(batch.state)
          || batch.revision && (batch.revision.id !== batch.revision_id || !Array.isArray(batch.revision.groups)))) throw new Error("The requested checkpoint result was not returned.");
      if (live) setValue(cp);
    }).catch(cause => { if (live) { setError((cause as Error).message); setStale((cause as { status?: number }).status === 409); } });
    return () => { live = false; };
  }, [sessionKey, checkpointId, cursor, generation, active]);
  if (!checkpointId) return <p>No checkpoint result has been recorded yet.</p>;
  if (error) return <div role="alert"><h2>{stale ? "Saved memory changed" : "Checkpoint result unavailable"}</h2><p>{stale ? "Reload the first page to read one consistent version. These pages have not been combined with newer data." : error}</p><button className="rd-btn rd-btn-sm" onClick={() => { setPages([""]); refresh(n => n + 1); }}>Reload first page</button></div>;
  if (!value) return <p role="status">Reading checkpoint result…</p>;
  const coverage = value.attempt_coverage;
  const counts = coverage && [coverage.eligible_records, coverage.summarized_records, coverage.pending_records].every(n => Number.isSafeInteger(n) && n >= 0)
    && coverage.summarized_records + coverage.pending_records === coverage.eligible_records ? coverage : null;
  const failed = value.summary_update?.state === "failed" || value.summary_update?.state === "partial";
  return <div>
    <p className="hint">{value.label || "Checkpoint"} · {new Date(value.created_at).toLocaleString()} · {value.id.slice(0, 8)}</p>
    <p><strong>{failed ? "Checkpoint failed" : value.summary_update?.state === "updated" ? "Summary updated" : value.summary_update?.state === "reused" ? "Saved summary reused" : "Saved checkpoint"}</strong></p>
    {counts && <dl className="rd-checkpoint-facts"><dt>Entries summarized</dt><dd>{counts.summarized_records} of {counts.eligible_records}</dd><dt>Still need summaries</dt><dd>{counts.pending_records}</dd><dt>Saved summaries</dt><dd>{value.pagination.total}</dd></dl>}
    {failed && <div className="rd-memory-failure" role="status"><strong>{value.summary_update?.reason === "source_changed" ? "The final source check failed" : "Summary update failed"}</strong><p>{checkpointFailure({ summary_update: value.summary_update ?? undefined })}. Saved batches remain readable below. This checkpoint is not ready for switching.</p></div>}
    {!value.batch_coverage_known ? <div><h2>Full batch list unavailable</h2><p>This older checkpoint did not record which summaries belonged to this attempt. Its linked summary is not a complete checkpoint result.</p>{value.linked_revision_id && <button className="rd-timeline-link" onClick={() => onRevision(value.linked_revision_id!)}>Read linked summary ↗</button>}</div> : <>
      <h2>Saved summaries from this attempt</h2><p className="hint">These are independent batch summaries. No combined summary was generated.</p>
      <MemoryPages page={value.pagination} noun="saved summaries" previous={pages.length > 1} onPrevious={() => setPages(p => p.slice(0, -1))} onNext={next => setPages(p => [...p, next])} />
      {value.batches.map((batch, index) => <section key={batch.revision_id}><div className="rd-memory-rowhead"><h2>Summary {value.pagination.offset + index + 1}</h2><button className="rd-timeline-link" onClick={() => onRevision(batch.revision_id)}>Open summary {batch.revision_id.slice(0, 8)} ↗</button></div>
        {batch.state !== "available" || !batch.revision?.summary ? <p>Saved evidence is unavailable. Affected summary content is withheld.</p> : <><p className="rd-memory-summary">{batch.revision.summary}</p><details><summary>Saved working context</summary><p>{batch.revision.overview}</p>{batch.revision.groups.map(group => <div key={group.field}><strong>{group.field}</strong>{group.claims.map(claim => <p key={claim.id ?? claim.record}>{claim.text}</p>)}</div>)}</details></>}
      </section>)}
    </>}
  </div>;
}
