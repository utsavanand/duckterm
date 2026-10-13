import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
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
  memory: { state: "available", span_count: 2, narratives: [], groups: [{ field: "decisions", claims: [{ id: "claim-one", text: "Keep the earlier decision", kind: "decision", state: "open", record: "decision:0", evidence: [{ source: "conversation:version", record: "turn-1", title: "Original conversation", kind: "conversation", role: "user", retained: true }] }] }] },
});
beforeEach(() => { get.mockResolvedValue(fixture()); vi.mocked(requestCheckpoint).mockResolvedValue({} as never); });
afterEach(() => { cleanup(); vi.resetAllMocks(); vi.useRealTimers(); });

it("reads combined saved claims without checkpointing; original evidence is lazy, paginated and escaped", async () => {
  render(<MemoryView sessionKey="remote::session" name="Test memory" />);
  await screen.findByText("Keep the earlier decision");
  expect(screen.getByText("decision: 1")).toBeVisible();
  expect(get).toHaveBeenCalledOnce();
  expect(requestCheckpoint).not.toHaveBeenCalled();
  get.mockResolvedValueOnce({ text: "<script>original</script>", next_offset: 25 });
  const details = screen.getByText("Original conversation · conversation").parentElement!;
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
