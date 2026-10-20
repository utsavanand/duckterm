import type { PreparationProgress } from "./memorySwitchTransport";
import { useNow } from "./useNow";

const stages: Record<PreparationProgress["stage"], string> = {
  queued: "Waiting to prepare", capturing: "Reading the conversation", summarizing: "Summarizing the conversation",
  writing: "Writing the summary", reviewing: "Reviewing the summary", repairing: "Revising the summary",
  checking: "Checking conversation records", reading: "Reading saved context", assembling: "Assembling the handoff",
  checking_sources: "Checking source availability", ready: "Ready to switch", failed: "Could not prepare the handoff",
  stale_source: "Work changed during preparation", incomplete_source: "Some prior history is unavailable", canceled: "Preparation canceled",
};
function ago(time: number | null | undefined, now: number): string {
  if (time == null) return "Not received yet";
  const seconds = Math.max(0, Math.floor((now - time) / 1000));
  if (seconds < 5) return "Just now";
  if (seconds < 60) return `${seconds} seconds ago`;
  const minutes = Math.floor(seconds / 60);
  return `${minutes} ${minutes === 1 ? "minute" : "minutes"} ago`;
}
export function PreparationProgressView({ progress, preparing, title, disconnected, lastResponseAt, message }: {
  progress?: PreparationProgress; preparing: boolean; title: string; disconnected: boolean;
  lastResponseAt: number | null; message: string;
}) {
  // Only this small status region subscribes to the clock, not the dashboard.
  const now = useNow();
  const known = progress?.completed !== undefined && progress.total !== undefined;
  const quiet = preparing && !disconnected && progress !== undefined && now - progress.updatedAt >= 120000;
  const stage = progress ? stages[progress.stage] : title;
  const heading = disconnected ? "Connection interrupted" : preparing ? stage : title;
  return <div className="rd-preparation-progress">
    <strong className={disconnected || quiet ? "rd-preparation-warning" : undefined}>{heading}</strong>
    <div className="rd-preparation-count"><span>Conversation records{known ? " checked" : ""}</span>
      <span>{known ? `${progress.completed!.toLocaleString()} / ${progress.total!.toLocaleString()}` : "Count not available yet"}</span></div>
    <div className={`rd-preparation-meter${known ? "" : " is-indeterminate"}${disconnected || (!preparing && progress?.stage !== "ready") ? " is-paused" : ""}`}
      role="progressbar" aria-label="Conversation records checked" aria-valuemin={known ? 0 : undefined}
      aria-valuemax={known ? progress.total : undefined} aria-valuenow={known ? progress.completed : undefined}
      aria-valuetext={known ? `${progress.completed} of ${progress.total} records checked` : "Count not available yet"}>
      <span style={known ? { width: `${progress.total! > 0 ? 100 * progress.completed! / progress.total! : 0}%` } : undefined} /></div>
    <p className="rd-preparation-hint">{known ? "Records checked in this attempt. This is not overall switch progress." : "The total appears when the conversation review is planned."}</p>
    <p>{disconnected ? `Cannot reach the server.${progress ? ` Last known stage: ${stage}.` : ""} Preparation may still be running.`
      : quiet ? `No progress update for ${ago(progress.updatedAt, now).replace(" ago", "")}. ${lastResponseAt !== null && now - lastResponseAt < 30000 ? "The server is responding; this step may still be working." : "No recent server response; the current status is unconfirmed."}` : message}</p>
    <div className="rd-preparation-clock"><span>Last server response: {ago(lastResponseAt, now)}</span>
      <span>Last progress: {ago(progress?.updatedAt, now)}</span></div>
  </div>;
}
