import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { useCheckpointAttempt } from "./checkpointRequests";
import { checkpointFailure } from "./checkpointState";
import "./checkpointProgress.css";

function Elapsed({ startedAt }: { startedAt: number }) {
  const [now, setNow] = useState(Date.now);
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, []);
  return <time aria-label="Elapsed time">{Math.max(0, Math.floor((now - startedAt) / 1000))}s</time>;
}
export function CheckpointProgress({ sessionKey }: { sessionKey: string }) {
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
  const failed = attempt.state === "unknown" || result?.saved === false || result?.summary_update?.state === "failed";
  const label = running ? "Updating summary…" : result ? (result.saved !== false && result.summary_update?.state === "partial" ? "Partial summary saved" : result.label) : "Result not confirmed";
  let detail = "The request is running. You can continue using this session.";
  if (attempt.state === "unknown") detail = "The connection ended before a result arrived. Check the latest checkpoint before trying again.";
  else if (result) {
    if (result.saved === false) detail = "The checkpoint was not saved. Any previously saved summary is unchanged.";
    else switch (result.summary_update?.state) {
      case "updated": detail = "A new summary revision was saved. This does not mean all conversation history was summarized."; break;
      case "reused": detail = "The existing summary revision was reused."; break;
      case "partial": detail = "Some history remains unsummarized. The saved summary is partial."; break;
      case "failed": detail = `${checkpointFailure(result)}. Any previously saved summary is unchanged.`; break;
      default: detail = "The request returned. See the latest checkpoint for its saved summary and coverage.";
    }
  }
  if (result?.export_reason) detail += " Markdown export is unavailable.";
  return <section ref={section} className={`rd-checkpoint-progress${failed ? " rd-checkpoint-progress-failure" : ""}`} aria-label="Checkpoint progress">
    <div className="rd-checkpoint-progress-title"><strong role="status">{label}</strong>{running && <Elapsed key={attempt.startedAt} startedAt={attempt.startedAt} />}</div>
    {running && <div className="rd-checkpoint-progress-track" role="progressbar" aria-label="Updating summary"><span /></div>}
    <p>{detail}</p>
  </section>;
}
