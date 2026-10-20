import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { sessionRef } from "./hostTransport";
import { CheckpointMemoryView } from "./CheckpointMemoryView";
import { memoryGet, type CheckpointMemory } from "./memoryViewApi";
vi.mock("./memoryViewApi", () => ({ memoryGet: vi.fn() }));
afterEach(() => { cleanup(); vi.resetAllMocks(); });
function fixture(): CheckpointMemory {
  return { scope: "checkpoint", session_key: "session", checkpoint: { id: "attempt", label: "manual", created_at: 1234,
    summary_update: { state: "failed", reason: "source_changed" },
    attempt_coverage: { state: "complete", scope: "refresh_attempt", available_records: 20, eligible_records: 20, summarized_records: 20, pending_records: 0, revision_ids: ["first", "second"] },
    batches: [{ revision_id: "first", state: "available", revision: { id: "first", created_at: 1234, source: "original", summary: "First independent batch", overview: null, groups: [], is_current: false, verified_at_save: true, withheld_claims: 0, coverage_at_save: {} } }],
    pagination: { snapshot: "snapshot", total: 2, offset: 0, returned: 1, next_cursor: "page-two" }, batch_coverage_known: true, combined_summary: null, combined_summary_reason: "independent_batch_summaries" } };
}
it("reads exact attempt batches with host routing and preserves failure even when all records saved", async () => {
  const remoteKey = sessionRef("remote", "session");
  const first = fixture(); first.session_key = remoteKey;
  vi.mocked(memoryGet).mockResolvedValueOnce(first);
  const open = vi.fn();
  render(<CheckpointMemoryView sessionKey={remoteKey} checkpointId="attempt" active onRevision={open} />);
  await screen.findByText("First independent batch");
  expect(screen.getByText("Checkpoint failed")).toBeVisible();
  expect(screen.getByText(/not ready for switching/)).toBeVisible();
  expect(memoryGet).toHaveBeenCalledExactlyOnceWith(remoteKey, "", { checkpoint: "attempt" });
  fireEvent.click(screen.getByRole("button", { name: "Open summary first ↗" }));
  expect(open).toHaveBeenCalledWith("first");
  const second = fixture(); second.session_key = remoteKey; second.checkpoint.pagination = { snapshot: "snapshot", total: 2, offset: 1, returned: 1, next_cursor: null };
  second.checkpoint.batches = [{ revision_id: "second", state: "unavailable", revision: null }];
  vi.mocked(memoryGet).mockResolvedValueOnce(second);
  fireEvent.click(screen.getByRole("button", { name: "Next page" }));
  await screen.findByText(/Affected summary content is withheld/);
  expect(screen.queryByText("First independent batch")).toBeNull();
  expect(memoryGet).toHaveBeenLastCalledWith(remoteKey, "", { checkpoint: "attempt", revisions_cursor: "page-two" });
});
it("keeps legacy unknown membership distinct and offers only its linked revision", async () => {
  const value = fixture(); value.checkpoint.batch_coverage_known = false; value.checkpoint.linked_revision_id = "older";
  vi.mocked(memoryGet).mockResolvedValue(value);
  const open = vi.fn(); render(<CheckpointMemoryView sessionKey="session" checkpointId="attempt" active onRevision={open} />);
  await screen.findByText("Full batch list unavailable");
  fireEvent.click(screen.getByRole("button", { name: "Read linked summary ↗" })); expect(open).toHaveBeenCalledWith("older");
  expect(screen.queryByText("First independent batch")).toBeNull();
});
it("discards a stale continuation and requires an explicit reload of the first page", async () => {
  vi.mocked(memoryGet).mockResolvedValueOnce(fixture()).mockRejectedValueOnce(Object.assign(new Error("Changed"), { status: 409 })).mockResolvedValueOnce(fixture());
  render(<CheckpointMemoryView sessionKey="session" checkpointId="attempt" active onRevision={vi.fn()} />);
  await screen.findByText("First independent batch"); fireEvent.click(screen.getByRole("button", { name: "Next page" }));
  await screen.findByText("Saved memory changed"); expect(screen.queryByText("First independent batch")).toBeNull();
  expect(memoryGet).toHaveBeenCalledTimes(2);
  fireEvent.click(screen.getByRole("button", { name: "Reload first page" })); await screen.findByText("First independent batch");
  expect(memoryGet).toHaveBeenLastCalledWith("session", "", { checkpoint: "attempt" });
});
it("rejects a response for a different checkpoint or session", async () => {
  const value = fixture(); value.session_key = "other"; vi.mocked(memoryGet).mockResolvedValue(value);
  render(<CheckpointMemoryView sessionKey="session" checkpointId="attempt" active onRevision={vi.fn()} />);
  await screen.findByRole("alert"); expect(screen.queryByText("First independent batch")).toBeNull();
});
