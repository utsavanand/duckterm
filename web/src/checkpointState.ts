import type { CheckpointRecord } from "./api";

export function checkpointHandoff(cp: Partial<CheckpointRecord>) {
  const handoff = cp.record?.handoff;
  return cp.saved === true && cp.format === "checkpoint_marker_v2" && handoff?.method === "maintained"
    && typeof handoff.packet_hash === "string" && handoff.packet_hash ? handoff : null;
}
export function checkpointHasNoSummary(cp: Partial<CheckpointRecord>) {
  return cp.saved === true && cp.format === "checkpoint_marker_v2" && cp.summary_state === "unavailable"
    && !cp.summary?.trim() && cp.record?.summary_ref == null && cp.coverage?.state === "retained"
    && !cp.reason_codes?.includes("source_missing");
}
export function checkpointSummaryMissing(cp: Partial<CheckpointRecord>) {
  return cp.summary_state === "unavailable" && !!cp.record?.summary_ref;
}
export function checkpointCanRetry(cp: Partial<CheckpointRecord>) {
  return !checkpointHandoff(cp) && cp.saved !== false
    && (cp.summary_update?.state === "failed" || checkpointHasNoSummary(cp));
}
// These describe the captured summary, never permission to stop a running agent.
export function checkpointStatus(cp: Partial<CheckpointRecord>) {
  const ready = cp.saved === true && cp.format === "checkpoint_marker_v2" && cp.summary_state === "ready" && cp.handoff_eligible === true
    && cp.coverage?.state === "retained" && cp.reason_codes?.length === 0 && !checkpointHandoff(cp)
    && cp.summary_update?.state !== "failed" && cp.summary_update?.state !== "partial";
  let label: string;
  if (cp.format === "fork_merge") label = "Merge note";
  else if (cp.saved === false) label = "Not saved";
  else if (cp.coverage?.state === "missing" || cp.reason_codes?.includes("source_missing")) label = "Incomplete coverage";
  else if (checkpointSummaryMissing(cp)) label = "Summary unavailable";
  else if (checkpointHandoff(cp)) label = "Brief assembled";
  else if (cp.summary_update?.state === "failed") label = "Summary update failed";
  else if (cp.summary_update?.state === "partial") label = "Partial summary";
  else if (cp.summary_update?.state === "updated") label = "Summary updated";
  else if (cp.summary_update?.state === "reused") label = "Summary already current";
  else if (checkpointHasNoSummary(cp)) label = "No summary generated";
  else label = ({ ready: "Summary saved", stale: "Summary needs updating", unavailable: "Summary unavailable", unverified: "Summary not verified", "legacy-unverified": "Summary not verified" } as Record<string, string>)[cp.summary_state ?? ""] ?? "Summary not verified";
  return { label, handoff: "Checked in Restart", ready };
}
const failures: Record<string, string> = {
  disabled: "Automatic summary generation is disabled",
  no_provider: "No summary provider is configured or available",
  provider_timeout: "The summary provider timed out",
  provider_failed: "The summary provider could not complete the request",
  invalid_summary: "The provider did not return a usable summary",
  invalid_context: "The generated context could not be verified",
  summary_unverified: "The summary did not pass validation",
  source_changed: "Work changed during the update; try again",
  source_unavailable: "Required source history could not be read",
  no_transcript: "No conversation text was available to summarize",
  update_failed: "The summary update could not be completed",
};
export function checkpointFailure(cp: Partial<CheckpointRecord>) {
  return failures[cp.summary_update?.reason ?? ""] ?? "The summary update could not be completed";
}
export function checkpointNotice(cp: Partial<CheckpointRecord>) {
  if (cp.saved === false) return "Checkpoint was not saved";
  const label = cp.summary_update?.state === "failed" ? `Summary update failed · ${checkpointFailure(cp)}` : checkpointStatus(cp).label;
  return `${label}${cp.export_reason ? " · Markdown export unavailable" : ""}`;
}
export const checkpointDate = (value?: number | null) => typeof value === "number" && value > 0
  ? new Date(value).toLocaleString() : "Unknown";
const reasons: Record<string, string> = {
  source_missing: "Some referenced history is unavailable.", source_changed: "Referenced history has changed.",
  summary_stale: "The summary predates the captured work.", summary_unavailable: "No usable summary is available for this record.",
  summary_unverified: "The summary has not been validated.", legacy_baseline_unreviewed: "This older record has no verified handoff coverage.",
  required_context_missing: "Required context is missing from the summary.",
  summary_coverage_partial: "Some history remains unsummarized.",
};
export function checkpointReasons(cp: Partial<CheckpointRecord>) {
  const specific = !checkpointSummaryMissing(cp) && (!!cp.summary_update || checkpointHasNoSummary(cp) || !!checkpointHandoff(cp));
  return [...new Set((cp.reason_codes ?? []).filter(code => !specific || !["summary_unavailable", "summary_unverified", "summary_coverage_partial"].includes(code))
    .map(code => reasons[code] ?? "Handoff coverage could not be verified."))];
}
