import type { CheckpointRecord } from "./api";
import { checkpointDate, checkpointReasons, checkpointStatus } from "./checkpointState";
import "./timeline.css";

export function CheckpointFacts({ checkpoint: cp, compact = false }: { checkpoint: Partial<CheckpointRecord>; compact?: boolean }) {
  const state = checkpointStatus(cp);
  return <>
    <dl className="rd-checkpoint-facts">
      <dt>{compact ? "Saved" : "Checkpoint"}</dt><dd>{cp.saved === false ? "Not saved" : compact ? checkpointDate(cp.created_at) : "Saved"}</dd>
      <dt>Summary source</dt><dd>{checkpointDate(cp.summary_source_at)}</dd>
      <dt>Handoff at save</dt><dd className={state.ready ? "ready" : ""}>{state.handoff}</dd>
    </dl>
    {compact ? <p className="rd-checkpoint-coverage">A switch checks current work and notes again before stopping the agent.</p> : <div className="rd-checkpoint-coverage">
      {cp.format === "fork_merge" ? <p>This merge note covers the child’s findings, not the full parent session.</p> : <>
        {checkpointReasons(cp).map(reason => <p key={reason}>{reason}</p>)}
        <p>{cp.coverage?.state === "retained" ? "Referenced event history is retained." : cp.coverage?.state === "missing" ? "Referenced event history is incomplete." : "History coverage is not verified."}
          {typeof cp.coverage?.events === "number" && ` ${cp.coverage.events} events${typeof cp.coverage.expected_events === "number" ? ` of ${cp.coverage.expected_events}` : ""}.`}</p>
        <p>This checkpoint does not back up the provider’s native conversation. A switch checks current work and notes again before stopping the agent.</p>
      </>}
      {cp.export_reason && <p>Checkpoint saved; Markdown export unavailable.</p>}
    </div>}
  </>;
}
export function CheckpointDetails({ checkpoint: cp, open = false }: { checkpoint: CheckpointRecord; open?: boolean }) {
  const status = checkpointStatus(cp);
  const r = cp.record;
  return <details className="rd-timeline-checkpoint" open={open || undefined}>
    <summary><span className="rd-timeline-cp-heading"><strong>{({ manual: "Manual checkpoint", "context-full": "Context checkpoint" } as Record<string, string>)[cp.label] ?? (cp.label || "Checkpoint")}</strong><span className={`rd-checkpoint-status ${status.ready ? "ready" : ""}`}>{status.label}</span></span>
      {cp.summary && <span className="rd-timeline-excerpt">{cp.summary}</span>}</summary>
    <CheckpointFacts checkpoint={cp} />
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
