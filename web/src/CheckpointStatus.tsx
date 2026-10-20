import type { CheckpointRecord } from "./api";
import { checkpointCanRetry, checkpointDate, checkpointFailure, checkpointHandoff, checkpointHasNoSummary, checkpointReasons, checkpointStatus, checkpointSummaryMissing } from "./checkpointState";
import "./timeline.css";
import { CheckpointFailureCoverage, checkpointFailureCoverage } from "./CheckpointFailureCoverage";

const snapshot = (value: unknown) => !!value && typeof value === "object" && "snapshot" in value && typeof value.snapshot === "string" && /^[a-f0-9]{64}$/.test(value.snapshot);
const count = (value: unknown): value is number => typeof value === "number" && Number.isSafeInteger(value) && value >= 0;
export function CheckpointFacts({ checkpoint: cp, compact = false, onMemory }: { checkpoint: Partial<CheckpointRecord>; compact?: boolean; onMemory?: (revision: string) => void }) {
  const state = checkpointStatus(cp);
  const handoff = checkpointHandoff(cp);
  const update = cp.summary_update?.state;
  const attemptCoverage = checkpointFailureCoverage(cp);
  const sourceFailure = update === "failed" && cp.summary_update?.reason === "source_changed";
  const spans = handoff?.method === "independent_spans";
  const missing = checkpointHasNoSummary(cp);
  const nativeSaved = snapshot(cp.record?.memory_source) || (Array.isArray(cp.record?.memory_sources) && cp.record.memory_sources.some(snapshot));
  const summary = checkpointSummaryMissing(cp) ? "The referenced summary is unavailable" : handoff ? handoff.summary_revision_id ? "Saved revision included" : "No saved summary used"
    : update === "failed" ? attemptCoverage?.summarized_records ? "Summaries saved · checkpoint failed" : "Update not completed" : missing ? "Not generated"
    : update === "updated" ? "New revision saved" : update === "reused" ? "Existing revision reused"
    : update === "partial" ? "Incomplete summary saved for inspection" : state.label;
  return <>
    <dl className="rd-checkpoint-facts">
      {compact && <><dt>Attempt</dt><dd>{checkpointDate(cp.created_at)}</dd></>}
      <dt>{handoff ? "Saved summary" : "Summary"}</dt><dd className={state.ready ? "ready" : ""}>{compact ? state.label : summary}</dd>
      {update === "failed" && <><dt>Reason</dt><dd>{checkpointFailure(cp)}</dd></>}
      {missing && !update && !handoff && <><dt>Failure reason</dt><dd>Not recorded by this older version</dd></>}
      {typeof cp.summary_source_at === "number" && cp.summary_source_at > 0 && <><dt>{update === "failed" && !sourceFailure ? "Previous summary" : "Summary source"}</dt><dd>{update === "failed" && !sourceFailure && "Kept · "}{checkpointDate(cp.summary_source_at)}</dd></>}
      {!compact && cp.summary_origin === "owner-reviewed" && <><dt>Summary origin</dt><dd>Owner-reviewed brief</dd></>}
      {!compact && handoff && <>
        {spans && count(handoff.summarized_records) && count(handoff.eligible_records) ? <><dt>Conversation summarized</dt><dd>{handoff.summarized_records.toLocaleString()} of {handoff.eligible_records.toLocaleString()} eligible records</dd></> : count(handoff.included_records) && <><dt>Included directly</dt><dd>{handoff.included_records.toLocaleString()} recent records</dd></>}
        {count(handoff.omitted_records) && <><dt>{spans ? "Reference-only history" : "Left for retrieval"}</dt><dd>{handoff.omitted_records.toLocaleString()} records</dd></>}
      </>}
    </dl>
    {update === "failed" && <CheckpointFailureCoverage checkpoint={cp} onMemory={onMemory} />}
    {compact ? update !== "failed" && <p className="rd-checkpoint-coverage">History is recorded automatically. Checkpoint requests a summary update now.</p> : <div className="rd-checkpoint-coverage">
      {cp.summary_origin === "owner-reviewed" && <p>This brief covers the owner-reviewed work and stated gaps.</p>}
      {cp.format === "fork_merge" ? <p>This merge note covers the child’s findings, not the full parent session.</p> : <>
        {handoff ? <><p>{spans ? "The brief uses recent span summaries, open memory claims and current work. Original records remain available through the read tools." : "The brief uses saved work and recent original records. No new summary was generated during preparation."}</p><p>This is a preparation record. Restart checks current work before switching; this entry does not mean a switch occurred.</p></>
          : update === "failed" ? null
          : missing ? <p>History was already being recorded automatically. This attempt did not produce a summary.</p>
          : update === "partial" ? <p>Some history remains unsummarized. This checkpoint is incomplete and not ready for handoff. The saved text is available for inspection.</p>
          : update === "reused" ? <p>No relevant source changed. The checkpoint points to the existing summary without another model call.</p>
          : update === "updated" ? <p>This checkpoint points to the updated summary revision; it does not create a second copy.</p> : null}
        {checkpointReasons(cp).map(reason => <p key={reason}>{reason}</p>)}
        <p>{cp.coverage?.state === "retained" ? "Referenced event history is retained." : cp.coverage?.state === "missing" ? "Referenced event history is incomplete." : "History coverage is not verified."}
          {typeof cp.coverage?.events === "number" && ` ${cp.coverage.events} events${typeof cp.coverage.expected_events === "number" ? ` of ${cp.coverage.expected_events}` : ""}.`}</p>
        {nativeSaved && <p>A native conversation snapshot was saved locally; project files are not included.</p>}
        {cp.record?.memory_retention === "unavailable" && <p>Some source history could not be retained.</p>}
      </>}
      {cp.export_reason && <p>Markdown export unavailable.</p>}
    </div>}
  </>;
}
export function CheckpointDetails({ checkpoint: cp, open = false, onRetry, retrying = false, onMemory }: { checkpoint: CheckpointRecord; open?: boolean; onRetry?: () => void; retrying?: boolean; onMemory?: (revision: string) => void }) {
  const status = checkpointStatus(cp);
  const r = cp.record;
  const title = cp.label === "manual" && checkpointCanRetry(cp) ? "Manual checkpoint attempt"
    : ({ manual: "Manual checkpoint", "context-full": "Context checkpoint" } as Record<string, string>)[cp.label] ?? (cp.label || "Checkpoint");
  return <details className="rd-timeline-checkpoint" open={open || undefined}>
    <summary><span className="rd-timeline-cp-heading"><strong>{title}</strong><span className={`rd-checkpoint-status ${status.ready ? "ready" : ""}`}>{status.label}</span></span>
      {cp.summary && <span className="rd-timeline-excerpt">{cp.summary}</span>}</summary>
    <CheckpointFacts checkpoint={cp} onMemory={onMemory} />
    {onMemory && typeof r?.summary_ref === "string" && r.summary_ref.trim() && <button className="rd-timeline-link" onClick={() => onMemory(r.summary_ref!)}>Read saved summary and evidence ↗</button>}
    {onRetry && checkpointCanRetry(cp) && <button className="rd-btn rd-btn-sm rd-btn-ghost rd-checkpoint-retry" disabled={retrying} onClick={onRetry}>{retrying ? "Updating summary…" : "Retry summary update"}</button>}
    {r && <div className="rd-checkpoint-coverage">
      {r.intention && <p>{r.intention}</p>}
      {r.repo && <p>{r.repo}{r.branch && ` · ${r.branch}`}</p>}
      {Array.isArray(r.prompts) && r.prompts.length > 0 && <details><summary>Original prompts ({r.prompts.length})</summary><ul>{r.prompts.map((p, i) => <li key={i}>{p}</li>)}</ul></details>}
      {Array.isArray(r.files) && r.files.length > 0 && <details><summary>Files ({r.files.length})</summary><ul>{r.files.map((f, i) => <li key={i}>{f.path} · {f.edits} edits</li>)}</ul></details>}
      {Array.isArray(r.commands) && r.commands.length > 0 && <details><summary>Commands ({r.commands.length})</summary><ul>{r.commands.map((c, i) => <li key={i}>{c}</li>)}</ul></details>}
      {Array.isArray(r.tools) && r.tools.length > 0 && <details><summary>Tools ({r.tools.length})</summary><ul>{r.tools.map((t, i) => <li key={i}>{t.tool} · {t.count}</li>)}</ul></details>}
    </div>}
  </details>;
}
