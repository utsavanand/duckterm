import { useLayoutEffect, useRef } from "react";
import { useCheckpointAttempt } from "./checkpointRequests";
import { checkpointAttemptFailed, checkpointFailure, checkpointStatus } from "./checkpointState";
import "./checkpointProgress.css";
import { checkpointMemoryAfterFailure } from "./CheckpointFailureCoverage";

export function CheckpointProgress({ sessionKey, compact = false }: { sessionKey: string; compact?: boolean }) {
  const attempt = useCheckpointAttempt(sessionKey);
  const section = useRef<HTMLElement>(null);
  const startedAt = attempt?.startedAt;
  useLayoutEffect(() => {
    // Tall session/recovery cards must not push immediate feedback below the fold.
    section.current?.scrollIntoView?.({ block: "nearest" });
  }, [sessionKey, startedAt]);
  if (!attempt) return null;
  const running = attempt.state === "running";
  const result = attempt.state === "complete" ? attempt.result : null;
  const failed = attempt.state === "unknown" || !!result && checkpointAttemptFailed(result);
  const progress = attempt.state === "running" ? attempt.progress : undefined;
  const stages = { checking: "Checking checkpoint…", writing: "Writing summary…", reviewing: "Reviewing summary…", repairing: "Correcting summary…", saving: "Saving checkpoint…" };
  const label = running ? progress ? stages[progress.stage] : "Updating summary…" : result ? result.label : "Result not confirmed";
  const measured = progress?.total !== undefined && progress.total > 0 && progress.completed !== undefined;
  const percent = measured ? 100 * progress.completed! / progress.total! : undefined;
  let detail = measured
    ? `${progress.completed!.toLocaleString()} of ${progress.total!.toLocaleString()} conversation entries checked. Earlier saved summaries are reused.`
    : "Checking the work to do. You can continue using this session.";
  if (running && progress?.stage === "saving") detail = "Finishing saved checkpoint checks. The result will appear when saving finishes.";
  if (attempt.state === "unknown") detail = attempt.recovering
    ? "The connection ended before a result arrived. Checking this checkpoint’s saved result."
    : "The connection ended before a result arrived. Check the latest checkpoint before trying again.";
  else if (result) {
    if (result.saved === false) detail = "The checkpoint was not saved. Any previously saved summary is unchanged.";
    else switch (result.summary_update?.state) {
      case "updated": detail = "A new summary revision was saved. This does not mean all conversation history was summarized."; break;
      case "reused": detail = "The existing summary revision was reused."; break;
      case "partial": detail = "Some history remains unsummarized. This checkpoint is incomplete and not ready for handoff. The saved text is available for inspection."; break;
      case "failed": detail = `${checkpointFailure(result)}. ${checkpointMemoryAfterFailure(result)}`; break;
      default: detail = "The request returned. See the latest checkpoint for its saved summary and coverage.";
    }
  }
  if (result?.export_reason) detail += " Markdown export is unavailable.";
  if (compact) return <section ref={section} className="rd-session-save-status" aria-label="Checkpoint progress">
    <span role="status">{running ? `${label}${measured ? ` · ${progress.completed} of ${progress.total} records` : ""}` : result ? checkpointStatus(result).label : attempt.state === "unknown" && attempt.recovering ? "Checking saved result…" : "Result not confirmed"}</span>
    {running && <div className={`rd-checkpoint-progress-track${measured ? " rd-checkpoint-progress-measured" : ""}`} role="progressbar" aria-label="Checkpoint entries checked" aria-valuemin={measured ? 0 : undefined} aria-valuemax={measured ? progress.total : undefined} aria-valuenow={measured ? progress.completed : undefined} aria-valuetext={label}>
      <span style={measured ? { width: `${percent}%` } : undefined} /></div>}
  </section>;
  return <section ref={section} className={`rd-checkpoint-progress${failed ? " rd-checkpoint-progress-failure" : ""}`} aria-label="Checkpoint progress">
    <div className="rd-checkpoint-progress-title"><strong role="status">{label}</strong></div>
    {running && <div className={`rd-checkpoint-progress-track${measured ? " rd-checkpoint-progress-measured" : ""}`} role="progressbar" aria-label="Checkpoint entries checked"
      aria-valuemin={measured ? 0 : undefined} aria-valuemax={measured ? progress.total : undefined}
      aria-valuenow={measured ? progress.completed : undefined} aria-valuetext={measured ? `${progress.completed} of ${progress.total} entries checked; ${label}` : label}>
      <span style={measured ? { width: `${percent}%` } : undefined} /></div>}
    <p>{detail}</p>
  </section>;
}
