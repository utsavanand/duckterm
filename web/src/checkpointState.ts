import type { CheckpointRecord } from "./api";

// These describe the captured boundary, never permission to stop a running agent.
export function checkpointStatus(cp: Partial<CheckpointRecord>) {
  if (cp.format === "fork_merge") return { label: "Merge note", handoff: "Not assessed", ready: false };
  const ready = cp.saved === true && cp.format === "checkpoint_marker_v2" && cp.summary_state === "ready" && cp.handoff_eligible === true
    && cp.coverage?.state === "retained" && cp.reason_codes?.length === 0;
  const labels: Record<string, string> = { ready: "Ready", stale: "Needs updating", unavailable: "Unavailable", unverified: "Not verified", "legacy-unverified": "Not verified" };
  return { label: ready ? "Ready" : cp.summary_state === "ready" ? "Incomplete coverage" : labels[cp.summary_state ?? ""] ?? "Not verified",
    handoff: ready ? "Ready" : "Not ready", ready };
}
export function checkpointNotice(cp: Partial<CheckpointRecord>) {
  if (cp.saved === false) return "Checkpoint was not saved";
  return `Checkpoint saved · ${checkpointStatus(cp).label.toLowerCase()}${cp.export_reason ? " · Markdown export unavailable" : ""}`;
}
export const checkpointDate = (value?: number | null) => typeof value === "number" && value > 0
  ? new Date(value).toLocaleString() : "Unknown";
const reasons: Record<string, string> = {
  source_missing: "Some referenced history is unavailable.", source_changed: "Referenced history has changed.",
  summary_stale: "The summary predates the captured work.", summary_unavailable: "A usable summary could not be generated.",
  summary_unverified: "The summary has not been validated.", legacy_baseline_unreviewed: "This older record has no verified handoff coverage.",
  required_context_missing: "Required context is missing from the summary.",
};
export function checkpointReasons(cp: Partial<CheckpointRecord>) {
  return [...new Set((cp.reason_codes ?? []).map(code => reasons[code] ?? "Handoff coverage could not be verified."))];
}
