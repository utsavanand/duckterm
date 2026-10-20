import type { CheckpointRecord } from "./api";

const count = (value: unknown): value is number => typeof value === "number" && Number.isSafeInteger(value) && value >= 0;
export function checkpointFailureCoverage(cp: Partial<CheckpointRecord>) {
  const coverage = cp.summary_update?.span_coverage;
  if (cp.summary_update?.state !== "failed" || !coverage || coverage.state !== "incomplete" || coverage.scope !== "refresh_attempt") return null;
  const { available_records: available, eligible_records: total, summarized_records: saved, pending_records: pending, reference_only_records: reference } = coverage;
  if (![available, total, saved, pending, reference].every(count) || total !== available || reference !== 0 || saved > total || pending !== total - saved
    || !Array.isArray(coverage.revision_ids) || !coverage.revision_ids.every(id => typeof id === "string" && /^[a-zA-Z0-9_-]{1,128}$/.test(id))) return null;
  return { ...coverage, revision_ids: [...new Set(coverage.revision_ids)] };
}
export function checkpointMemoryAfterFailure(cp: Partial<CheckpointRecord>) {
  if (cp.summary_update?.reason === "source_changed") return "Saved memory may have advanced before the final check failed. This result does not confirm a usable checkpoint. Read saved memory to inspect its current state.";
  return "Any previously saved summary is unchanged.";
}
export function CheckpointFailureCoverage({ checkpoint, onMemory }: { checkpoint: Partial<CheckpointRecord>; onMemory?: (revision: string) => void }) {
  const coverage = checkpointFailureCoverage(checkpoint);
  const allSaved = !!coverage && coverage.eligible_records > 0 && coverage.pending_records === 0;
  return <div className="rd-checkpoint-coverage rd-checkpoint-attempt">
    {coverage ? <>
      <dl className="rd-checkpoint-facts"><dt>Entries summarized</dt><dd><strong>{coverage.summarized_records.toLocaleString()} of {coverage.eligible_records.toLocaleString()} saved</strong></dd></dl>
      <p>{allSaved ? checkpoint.summary_update?.reason === "source_changed"
        ? "All selected entries have saved summaries, but the final source check failed."
        : "All selected entries have saved summaries, but the checkpoint did not complete."
        : coverage.pending_records > 0 ? `${coverage.pending_records.toLocaleString()} entries still need summaries.` : "No entries were selected in this attempt."}</p>
    </> : <p>This attempt did not report usable saved-entry counts or revision links.</p>}
    <p className="rd-checkpoint-attempt-warning">This checkpoint failed. It is not ready for switching.</p>
    {coverage && coverage.revision_ids.length > 0 && <details>
      <summary>Saved revisions ({coverage.revision_ids.length})</summary>
      <div className="rd-checkpoint-revision-links">{coverage.revision_ids.map((id, index) => onMemory
        ? <button key={id} className="rd-timeline-link" title={id} onClick={() => onMemory(id)}>Summary {index + 1} · {id.slice(0, 8)} ↗</button>
        : <span key={id} title={id}>Summary {index + 1} · {id.slice(0, 8)}</span>)}</div>
    </details>}
    <p>{coverage ? checkpointMemoryAfterFailure(checkpoint) : "Read saved memory to inspect its current state."}</p>
    <p className="rd-checkpoint-attempt-help">Counts and revision links cover this attempt only, not earlier saved work. An entry is a captured conversation or activity record, not necessarily a full turn.</p>
  </div>;
}
