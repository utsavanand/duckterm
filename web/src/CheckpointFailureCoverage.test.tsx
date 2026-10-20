import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import type { CheckpointAttemptCoverage, CheckpointRecord } from "./api";
import { CheckpointFacts } from "./CheckpointStatus";
import { checkpointFailureCoverage } from "./CheckpointFailureCoverage";
import { checkpointStatus } from "./checkpointState";

afterEach(cleanup);
const coverage: CheckpointAttemptCoverage = { state: "incomplete", scope: "refresh_attempt", available_records: 20, eligible_records: 20, summarized_records: 12, pending_records: 8, reference_only_records: 0, revision_ids: ["batch-a", "batch-b"] };
const failed = (span = coverage): Partial<CheckpointRecord> => ({
  saved: true, format: "checkpoint_marker_v2", summary_state: "ready", handoff_eligible: true,
  coverage: { state: "retained" }, reason_codes: [], summary_source_at: 1000,
  summary_update: { state: "failed", reason: "summary_unverified", revision_id: null, span_coverage: span },
});
it("shows accepted work from a failed attempt and routes each exact revision without promoting readiness", () => {
  const cp = failed(), open = vi.fn();
  render(<CheckpointFacts checkpoint={cp} compact onMemory={open} />);
  expect(screen.getByText("12 of 20 saved")).toBeVisible();
  expect(screen.getByText("8 entries still need summaries.")).toBeVisible();
  expect(checkpointStatus(cp).ready).toBe(false);
  fireEvent.click(screen.getByText("Saved revisions (2)"));
  fireEvent.click(screen.getByRole("button", { name: "Summary 2 · batch-b ↗" }));
  expect(open).toHaveBeenCalledExactlyOnceWith("batch-b");
  expect(cp.summary_update?.revision_id).toBeNull();
});
it("keeps a late source failure failed even when all entries were saved, without promising rollback", () => {
  const cp = failed({ ...coverage, summarized_records: 20, pending_records: 0 });
  cp.summary_update!.reason = "source_changed";
  cp.summary_update!.diagnostic = "refresh_result:source_changed";
  render(<CheckpointFacts checkpoint={cp} compact />);
  expect(screen.getByText("20 of 20 saved")).toBeVisible();
  expect(screen.getByText(/All selected entries have saved summaries, but the final source check failed/)).toBeVisible();
  expect(screen.getByText(/Saved memory may have advanced/)).toBeVisible();
  expect(screen.queryByText(/entries still need|unchanged|Kept ·/)).toBeNull();
  expect(checkpointStatus(cp).ready).toBe(false);
});
it("keeps zero saved entries explicit and omits empty revision controls", () => {
  render(<CheckpointFacts checkpoint={failed({ ...coverage, summarized_records: 0, pending_records: 20, revision_ids: [] })} />);
  expect(screen.getByText("0 of 20 saved")).toBeVisible();
  expect(screen.getByText("20 entries still need summaries.")).toBeVisible();
  expect(screen.queryByText(/Saved revisions/)).toBeNull();
});
it.each([
  { pending_records: -1 }, { pending_records: 9 }, { summarized_records: 21 },
  { summarized_records: 1.5 }, { available_records: 30 }, { reference_only_records: 1 },
  { scope: "session" }, { state: "complete" }, { revision_ids: ["../other"] },
])("withholds invalid or session-wide coverage: %j", patch => {
  const cp = failed({ ...coverage, ...patch } as CheckpointAttemptCoverage);
  expect(checkpointFailureCoverage(cp)).toBeNull();
  render(<CheckpointFacts checkpoint={cp} compact />);
  expect(screen.queryByText(/of 20 saved|Saved revisions/)).toBeNull();
  expect(screen.getByText(/did not report usable saved-entry counts/)).toBeVisible();
});
it("does not show attempt-only failures for a successful result carrying unrelated coverage", () => {
  const cp = failed(); cp.summary_update!.state = "updated";
  expect(checkpointFailureCoverage(cp)).toBeNull();
  render(<CheckpointFacts checkpoint={cp} />);
  expect(screen.queryByText(/This checkpoint failed|of 20 saved/)).toBeNull();
});
