import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { MemoryView } from "./MemoryView";
import { CheckpointAge, publishCheckpointClock } from "./CheckpointAge";
import { memoryGet, type SavedMemory } from "./memoryViewApi";
import { requestCheckpoint } from "./checkpointRequests";

vi.mock("./memoryViewApi", () => ({ memoryGet: vi.fn() }));
vi.mock("./checkpointSelection", () => ({ chooseCheckpoint: (key: string) => requestCheckpoint(key) }));
vi.mock("./checkpointRequests", () => ({ requestCheckpoint: vi.fn(), useCheckpointAttempt: () => undefined }));
const get = vi.mocked(memoryGet);
const fixture = (): SavedMemory => ({
  state: "available", current_revision_id: "latest", checkpoint_clock: { state: "known", succeeded_at: Date.now() - 3_600_000, checkpoint_id: "success" },
  revisions: [{ id: "latest", created_at: 2000 }, { id: "older", created_at: 1000 }], revisions_truncated: false,
  revision: { id: "latest", source: "revision:latest", created_at: 2000, summary: "Latest span narrative", overview: null, is_current: true, verified_at_save: true, groups: [], withheld_claims: 0, coverage_at_save: {} },
  memory: { state: "available", span_count: 2, narratives: [], groups: [{ field: "decisions", claims: [{ id: "claim-one", text: "Keep the earlier decision", kind: "decision", state: "open", record: "decision:0", evidence: [{ source: "conversation:version", record: "turn-1", title: "Original conversation", kind: "conversation", role: "user", retained: true }] }] },
    // Pre-taxonomy kinds count under their current names.
    { field: "unfinished", claims: [{ id: "claim-two", text: "Ship the backup button", kind: "unfinished", state: "open", record: "unfinished:0", evidence: [] }] },
    { field: "constraints", claims: [{ id: "claim-three", text: "Locks fail instantly", kind: "known_issue", state: "open", record: "constraints:0", evidence: [] }] }] },
});
beforeEach(() => { get.mockResolvedValue(fixture()); vi.mocked(requestCheckpoint).mockResolvedValue({} as never); });
afterEach(() => { cleanup(); vi.resetAllMocks(); vi.useRealTimers(); });

it("reads combined saved claims without checkpointing; original evidence is lazy, paginated and escaped", async () => {
  render(<MemoryView sessionKey="remote::session" name="Test memory" />);
  await screen.findByText("Keep the earlier decision");
  expect(screen.getByText("Intent: 1 task")).toBeVisible();
  expect(screen.getByText("Rules: 1 decision")).toBeVisible();
  expect(screen.getByText("Facts: 1 known issue")).toBeVisible();
  // Sections follow the categories, not the six storage fields.
  const section = (name: string) => screen.getByRole("heading", { name, level: 2 }).parentElement!;
  expect(within(section("Intent")).getByText("Ship the backup button")).toBeVisible();
  expect(within(section("Intent")).getByText("task · open")).toBeVisible();
  expect(within(section("Rules")).getByText("Keep the earlier decision")).toBeVisible();
  expect(within(section("Facts")).getByText("known issue · open")).toBeVisible();
  expect(get).toHaveBeenCalledOnce();
  expect(requestCheckpoint).not.toHaveBeenCalled();
  get.mockResolvedValueOnce({ text: "<script>original</script>", next_offset: 25 });
  // One collapsed line per item; each citation is labelled by role and record.
  expect(screen.getByText("Evidence: 1 message")).toBeVisible();
  expect(screen.queryByText("Original conversation · conversation")?.closest("summary")).toBeFalsy();
  const details = screen.getByText("user · #turn-1").parentElement!;
  details.setAttribute("open", ""); fireEvent(details, new Event("toggle"));
  await screen.findByText("<script>original</script>");
  expect(document.querySelector("script")).toBeNull();
  expect(get).toHaveBeenLastCalledWith("remote::session", "read", { source: "conversation:version", record: "turn-1" });
  get.mockResolvedValueOnce({ text: " More evidence.", next_offset: null });
  fireEvent.click(screen.getByRole("button", { name: "Read more" }));
  await screen.findByText("<script>original</script> More evidence.");
  expect(get).toHaveBeenLastCalledWith("remote::session", "read", { source: "conversation:version", record: "turn-1", offset: "25" });
  fireEvent.click(screen.getByRole("button", { name: "Checkpoint" }));
  await waitFor(() => expect(requestCheckpoint).toHaveBeenCalledExactlyOnceWith("remote::session"));
});

it("selects an exact historical span and withholds previous text if that read fails", async () => {
  render(<MemoryView sessionKey="history" name="Historical memory" />);
  await screen.findByText("Keep the earlier decision");
  const historical = fixture(); delete historical.memory;
  historical.revision = { ...historical.revision!, id: "older", is_current: false, summary: "Earlier exact summary" };
  get.mockResolvedValueOnce(historical);
  fireEvent.change(screen.getByRole("combobox"), { target: { value: "older" } });
  await screen.findByText("Earlier exact summary");
  expect(screen.queryByText("Keep the earlier decision")).toBeNull();
  expect(get).toHaveBeenLastCalledWith("history", "", { revision: "older" });
  get.mockRejectedValueOnce(new Error("Evidence no longer available"));
  fireEvent.change(screen.getByRole("combobox"), { target: { value: "latest" } });
  await screen.findByRole("alert");
  expect(screen.queryByText("Earlier exact summary")).toBeNull();
  expect(requestCheckpoint).not.toHaveBeenCalled();
});

it("refreshes after its own checkpoint completion without resetting success age on a failed attempt", async () => {
  const memory = fixture(); memory.checkpoint_clock.succeeded_at = Date.now() - 3_600_000;
  memory.latest_checkpoint = { id: "failed", created_at: Date.now(), label: "manual", summary_update: { state: "failed", reason: "provider_timeout" } };
  get.mockResolvedValue(memory);
  render(<MemoryView sessionKey="refresh" name="Memory" />);
  await screen.findByText("Last checkpointed 1h ago");
  expect(screen.getByText("Latest summary update failed")).toBeVisible();
  act(() => { window.dispatchEvent(new CustomEvent("duckterm-checkpoint", { detail: "other-host::refresh" })); });
  expect(get).toHaveBeenCalledOnce();
  act(() => { window.dispatchEvent(new CustomEvent("duckterm-checkpoint", { detail: "refresh" })); });
  await waitFor(() => expect(get).toHaveBeenCalledTimes(2));
  expect(screen.getByText("Last checkpointed 1h ago")).toBeVisible();
});

it("uses the reported clock after late source failure without claiming a rollback", async () => {
  const memory = fixture(); memory.checkpoint_clock.succeeded_at = Date.now() - 60_000;
  memory.latest_checkpoint = { id: "late", created_at: Date.now(), label: "manual", summary_update: { state: "failed", reason: "source_changed", diagnostic: "refresh_result:native_file_changed" } };
  get.mockResolvedValue(memory);
  render(<MemoryView sessionKey="late-source-memory" name="Memory" />);
  await screen.findByText(/Saved memory may have advanced/);
  expect(screen.queryByText(/clock has not reset|summary is unchanged/)).toBeNull();
  expect(screen.getByText("Last checkpointed 1m ago")).toBeVisible();
});

it("keeps clocks host-scoped and distinguishes never, unknown and invalid timestamps", () => {
  publishCheckpointClock("clock", { state: "never", succeeded_at: null, checkpoint_id: null });
  publishCheckpointClock("remote::clock", { state: "known", succeeded_at: Date.now() - 3 * 86400000, checkpoint_id: "old" });
  const view = render(<><CheckpointAge sessionKey="clock" /><CheckpointAge sessionKey="remote::clock" /></>);
  expect(screen.getByText("Never successfully checkpointed")).toBeVisible();
  expect(screen.getByText("Last checkpointed 3d ago")).toHaveClass("stale");
  act(() => publishCheckpointClock("clock", { state: "known", succeeded_at: null, checkpoint_id: "invalid" }));
  expect(screen.getByText("Checkpoint age unknown")).toBeVisible();
  expect(screen.queryByText("Last checkpointed just now")).toBeNull();
  view.unmount();
});

it("withholds aggregate claims when combined evidence cannot be verified", async () => {
  const saved = fixture(); saved.memory = { state: "unavailable", reason: "evidence_unavailable", groups: [], narratives: [] };
  get.mockResolvedValueOnce(saved);
  render(<MemoryView sessionKey="unavailable" name="Unavailable memory" />);
  await screen.findByText(/Combined memory is unavailable/);
  expect(screen.queryByText("Keep the earlier decision")).toBeNull();
  expect(screen.getByText("Latest span narrative")).toBeVisible();
  expect(requestCheckpoint).not.toHaveBeenCalled();
});

it("opens a linked revision outside the recent list and reads its own evidence on the same host", async () => {
  const saved = fixture();
  saved.revisions = [{ id: "latest", created_at: 2000 }]; saved.revisions_truncated = true;
  saved.revision = { ...saved.revision!, id: "older-exact", created_at: 500, is_current: false, summary: "Historical linked summary", groups: saved.memory!.groups };
  const onBack = vi.fn();
  get.mockResolvedValueOnce(saved);
  render(<MemoryView sessionKey="remote::same" name="Remote memory" initialRevision="older-exact" onBack={onBack} />);
  await screen.findByText("Historical linked summary");
  expect(get).toHaveBeenCalledExactlyOnceWith("remote::same", "", { revision: "older-exact" });
  expect(screen.getByRole("combobox")).toHaveValue("older-exact");
  const evidence = screen.getByText("user · #turn-1").parentElement!;
  get.mockResolvedValueOnce({ text: "Original older evidence", next_offset: null });
  evidence.setAttribute("open", ""); fireEvent(evidence, new Event("toggle"));
  await screen.findByText("Original older evidence");
  expect(get).toHaveBeenLastCalledWith("remote::same", "read", { source: "conversation:version", record: "turn-1" });
  fireEvent.click(screen.getByRole("button", { name: "← Timeline" }));
  expect(onBack).toHaveBeenCalledOnce();
  expect(requestCheckpoint).not.toHaveBeenCalled();
});

it("rejects an unexpected latest revision instead of substituting it for the linked revision", async () => {
  render(<MemoryView sessionKey="same" name="Memory" initialRevision="older-exact" />);
  await screen.findByRole("alert");
  expect(screen.queryByText("Latest span narrative")).toBeNull();
  expect(screen.queryByText("Keep the earlier decision")).toBeNull();
  expect(get).toHaveBeenCalledExactlyOnceWith("same", "", { revision: "older-exact" });
  expect(requestCheckpoint).not.toHaveBeenCalled();
});

it("keeps a missing historical revision selected without fetching current memory", async () => {
  get.mockRejectedValueOnce(new Error("Summary revision is unavailable in this session"));
  render(<MemoryView sessionKey="remote::same" name="Memory" initialRevision="missing" />);
  await screen.findByRole("alert");
  expect(screen.getByRole("combobox")).toHaveValue("missing");
  expect(get).toHaveBeenCalledExactlyOnceWith("remote::same", "", { revision: "missing" });
  expect(requestCheckpoint).not.toHaveBeenCalled();
});

it("pages saved knowledge without appending mixed snapshots and reloads explicitly on conflict", async () => {
  const first = fixture(); first.memory!.pagination = { snapshot: "fixed", total: 27, offset: 0, returned: 25, next_cursor: "next-claims" };
  get.mockResolvedValueOnce(first);
  render(<MemoryView sessionKey="remote::paged" name="Paged memory" />);
  await screen.findByText("1–25 of 27 saved items");
  get.mockRejectedValueOnce(Object.assign(new Error("Changed"), { status: 409 }));
  fireEvent.click(screen.getByRole("button", { name: "Next page" }));
  await screen.findByText(/Saved memory changed/);
  expect(screen.queryByText("Keep the earlier decision")).toBeNull();
  expect(get).toHaveBeenLastCalledWith("remote::paged", "", { claims_cursor: "next-claims" });
  get.mockResolvedValueOnce(first);
  fireEvent.click(screen.getByRole("button", { name: "Reload first page" }));
  await screen.findByText("Keep the earlier decision");
  expect(get).toHaveBeenLastCalledWith("remote::paged", "", {});
  expect(requestCheckpoint).not.toHaveBeenCalled();
});

it("pages revision history and opens the exact historical revision", async () => {
  const first = fixture(); first.revisions_pagination = { snapshot: "history", total: 22, offset: 0, returned: 20, next_cursor: "older-page" };
  get.mockResolvedValue(first);
  render(<MemoryView sessionKey="history-pages" name="History" />);
  await screen.findByText("Keep the earlier decision");
  fireEvent.click(screen.getByRole("button", { name: "Revision history" }));
  await screen.findByText("1–20 of 22 revisions");
  const second = fixture(); second.revisions = [{ id: "oldest-exact", created_at: 1 }];
  second.revisions_pagination = { snapshot: "history", total: 22, offset: 20, returned: 2, next_cursor: null };
  get.mockResolvedValueOnce(second); fireEvent.click(screen.getByRole("button", { name: "Next page" }));
  await screen.findByText("21–22 of 22 revisions");
  expect(get).toHaveBeenLastCalledWith("history-pages", "", { revisions_cursor: "older-page" });
  const exact = fixture(); exact.revision = { ...exact.revision!, id: "oldest-exact", summary: "Exact historical evidence" }; delete exact.memory;
  get.mockResolvedValueOnce(exact); fireEvent.click(screen.getByRole("button", { name: "Open summary oldest-e ↗" }));
  await screen.findByText("Exact historical evidence");
  expect(get).toHaveBeenLastCalledWith("history-pages", "", { revision: "oldest-exact" });
});
