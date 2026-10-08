import { expect, it } from "vitest";
import { checkpointDate, checkpointNotice, checkpointReasons, checkpointStatus } from "./checkpointState";
import type { CheckpointRecord } from "./api";
const ready: Partial<CheckpointRecord> = { saved: true, format: "checkpoint_marker_v2", summary_state: "ready", handoff_eligible: true, coverage: { state: "retained" }, reason_codes: [] };
it("never infers readiness from a recent save or a summary alone", () => {
  expect(checkpointStatus({ created_at: Date.now(), summary: "All done" }).ready).toBe(false);
  expect(checkpointStatus({ ...ready, coverage: { state: "missing" } }).ready).toBe(false);
  expect(checkpointStatus({ ...ready, handoff_eligible: false }).ready).toBe(false);
  expect(checkpointStatus({ ...ready, format: "future" }).ready).toBe(false);
  expect(checkpointStatus({ ...ready, saved: false }).ready).toBe(false);
  expect(checkpointStatus(ready).handoff).toBe("Checked in Restart");
});
it("separates saved success, export failure, stale summary and merge scope", () => {
  expect(checkpointNotice({ saved: true, summary_state: "unavailable" })).toBe("Summary unavailable");
  expect(checkpointNotice({ ...ready, export_reason: "markdown_unavailable" })).toContain("Summary saved · Markdown export unavailable");
  expect(checkpointStatus({ summary_state: "stale" }).label).toBe("Summary needs updating");
  expect(checkpointStatus({ format: "fork_merge" }).label).toBe("Merge note");
  expect(checkpointDate(null)).toBe("Unknown");
  expect(checkpointReasons({ reason_codes: ["secret error /private/path"] })).toEqual(["Handoff coverage could not be verified."]);
});
