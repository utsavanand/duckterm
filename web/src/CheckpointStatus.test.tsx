import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import type { CheckpointRecord } from "./api";
import { CheckpointDetails } from "./CheckpointStatus";
import { checkpointNotice, checkpointStatus } from "./checkpointState";

// Owner-reported saved metadata, with identities replaced for this fixture.
export function checkpoint(prepared = false): CheckpointRecord {
  return {
    id: "test-checkpoint", label: prepared ? "Prepared harness switch" : "manual",
    saved: true, format: "checkpoint_marker_v2", summary: "", created_at: 1791425767179,
    summary_state: "unavailable", summary_source_at: null, handoff_eligible: false,
    reason_codes: ["summary_unavailable"], coverage: { state: "retained", events: 635, expected_events: 635 },
    record: { summary_ref: null, prompts: [], files: [], tools: [], commands: [], event_count: 635,
      memory_sources: [{ runtime: "claude-code", native_id: "test-native", snapshot: "a".repeat(64) }],
      ...(prepared ? { handoff: { method: "maintained", summary_revision_id: null,
        included_records: 20, omitted_records: 1230, packet_hash: "b".repeat(64) } } : {}),
    },
  };
}
afterEach(cleanup);
it("calls an old missing-summary checkpoint an attempt without inventing its failure cause", () => {
  const cp = checkpoint(), retry = vi.fn(); render(<CheckpointDetails checkpoint={cp} onRetry={retry} open />);
  expect(screen.getByText("Manual checkpoint attempt")).toBeVisible();
  expect(screen.getByText("No summary generated")).toBeVisible();
  expect(screen.getByText("Not recorded by this older version")).toBeVisible();
  expect(screen.queryByText("Not ready")).toBeNull();
  expect(screen.queryByText("Unknown")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Retry summary update" }));
  expect(retry).toHaveBeenCalledOnce();
  expect(checkpointStatus(cp).ready).toBe(false);
});
it("shows assembly and direct-original counts separately from summary generation or switching", () => {
  const cp = checkpoint(true); render(<CheckpointDetails checkpoint={cp} onRetry={vi.fn()} open />);
  expect(screen.getByText("Brief assembled")).toBeVisible();
  expect(screen.getByText("No saved summary used")).toBeVisible();
  expect(screen.getByText("20 recent records")).toBeVisible();
  expect(screen.getByText("1,230 records")).toBeVisible();
  expect(screen.getByText(/this entry does not mean a switch occurred/)).toBeVisible();
  expect(screen.queryByRole("button", { name: "Retry summary update" })).toBeNull();
  expect(checkpointStatus(cp).ready).toBe(false);
});
it("preserves the prior summary age when an update failed and uses a fixed error label", () => {
  const cp = checkpoint(); cp.summary = "Previously validated work"; cp.summary_source_at = 1000;
  cp.summary_update = { state: "failed", reason: "provider_timeout" };
  render(<CheckpointDetails checkpoint={cp} open />);
  expect(screen.getByText("Summary update failed")).toBeVisible();
  expect(screen.getByText("The summary provider timed out")).toBeVisible();
  expect(screen.getByText(`Kept · ${new Date(1000).toLocaleString()}`)).toBeVisible();
  expect(screen.getByText(cp.summary)).toBeVisible();
  expect(checkpointNotice(cp)).toBe("Summary update failed · The summary provider timed out");
  expect(checkpointNotice({ ...cp, summary_update: { state: "failed", reason: "secret /private/path" } })).not.toContain("private");
});
it.each([["updated", "Summary updated"], ["reused", "Summary already current"], ["partial", "Partial summary"]] as const)("labels %s independently of stop permission", (state, label) => {
  const cp = checkpoint(); cp.summary_update = { state }; cp.summary = "Summary text";
  render(<CheckpointDetails checkpoint={cp} open />);
  expect(screen.getByText(label)).toBeVisible();
  expect(checkpointStatus(cp).ready).toBe(false);
  expect(screen.queryByText("Not ready")).toBeNull();
});
it("shows retained native snapshots without claiming a project backup", () => {
  render(<CheckpointDetails checkpoint={checkpoint()} open />);
  expect(screen.getByText(/A native conversation snapshot was saved/)).toHaveTextContent("project files are not included");
  expect(screen.queryByText(/does not back up the provider/)).toBeNull();
});
it("does not hide missing source history behind brief assembly", () => {
  const cp = checkpoint(true); cp.coverage = { state: "missing", events: 600, expected_events: 635 };
  cp.reason_codes = ["summary_unavailable", "source_missing"];
  render(<CheckpointDetails checkpoint={cp} open />);
  expect(screen.getByText("Incomplete coverage")).toBeVisible();
  expect(screen.getByText("Some referenced history is unavailable.")).toBeVisible();
  expect(checkpointStatus(cp).ready).toBe(false);
});
it("does not turn a missing saved revision into a successful update or an invented generation failure", () => {
  const cp = checkpoint(); cp.record.summary_ref = "removed-revision";
  cp.summary_update = { state: "updated", revision_id: "removed-revision" };
  render(<CheckpointDetails checkpoint={cp} onRetry={vi.fn()} open />);
  expect(screen.getByText("Summary unavailable")).toBeVisible();
  expect(screen.getByText("The referenced summary is unavailable")).toBeVisible();
  expect(screen.queryByText("No summary generated")).toBeNull();
  expect(screen.queryByText("Summary updated")).toBeNull();
  expect(checkpointStatus(cp).ready).toBe(false);
});
