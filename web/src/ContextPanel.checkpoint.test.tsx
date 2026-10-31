import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { api, CheckpointRecord, ContextEvidence } from "./api";
import { memoryGet } from "./memoryViewApi";
import { ContextPanel } from "./ContextPanel";
import { edition, FULL } from "./test/editions";
import type { SessionView } from "./types";
const { toast } = vi.hoisted(() => ({ toast: vi.fn() }));
vi.mock("./api", () => ({ api: { checkpoints: vi.fn(), checkpoint: vi.fn(), sendInput: vi.fn() } }));
vi.mock("./memoryViewApi", () => ({ memoryGet: vi.fn() }));
vi.mock("./ui", () => ({ useToast: () => toast }));
vi.mock("./Duck", () => ({ Duck: () => null, duckPhrase: () => "idle", poseFor: () => "idle" }));
vi.mock("./FileEditModal", () => ({ FileEditModal: () => null }));
const session = { key: "one", state: "idle", label: "Test", startedAt: 1, contextTokens: 190000, ptyOwned: true, runtime: "codex", model: "gpt-6-astra" } as SessionView;
const cp: CheckpointRecord = { id: "c", label: "manual", created_at: 1000, summary: "Saved summary", saved: true, summary_state: "unavailable", summary_update: { state: "failed", reason: "summary_unverified" }, record: { prompts: [], files: [], tools: [], event_count: 0 } };
const evidence: ContextEvidence = { state: "available", reason: null, context_window_tokens: 258400, last_compaction_at: "2026-10-19T10:00:00Z", history_scope: "recent" };
beforeEach(() => { vi.mocked(api.checkpoints).mockResolvedValue({ checkpoints: [cp] }); vi.mocked(memoryGet).mockResolvedValue({ revisions: [], next_cursor: null }); Object.defineProperty(document, "visibilityState", { configurable: true, value: "visible" }); });
afterEach(() => { cleanup(); vi.resetAllMocks(); });
it("opens the exact dated revision without checkpointing or generating memory", async () => {
  vi.mocked(memoryGet).mockResolvedValue({ revisions: [{ id: "new", created_at: 2000 }, { id: "old", created_at: 1000 }], next_cursor: null });
  const open = vi.fn();
  render(<ContextPanel session={session} onMemory={open} />, { wrapper: edition(FULL) });
  const buttons = await screen.findAllByRole("button", { name: "View memory ↗" });
  fireEvent.click(buttons[1]); expect(open).toHaveBeenCalledWith("old");
  expect(memoryGet).toHaveBeenCalledWith("one", "revisions", { limit: "20" });
  expect(api.checkpoint).not.toHaveBeenCalled(); expect(api.sendInput).not.toHaveBeenCalled();
  expect(screen.queryByText("Last successful save")).toBeNull();
});
it("shows empty memory distinctly from failed loading and allows reload", async () => {
  vi.mocked(memoryGet).mockRejectedValueOnce(new Error("offline")).mockResolvedValue({ revisions: [], next_cursor: null });
  render(<ContextPanel session={session} />, { wrapper: edition(FULL) });
  await screen.findByText("Couldn’t load saved memories.");
  expect(screen.queryByText("No saved memories yet.")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Reload memories" }));
  await screen.findByText("No saved memories yet.");
});
it("loads older entries and refreshes the first page when a checkpoint completes", async () => {
  vi.mocked(memoryGet).mockResolvedValueOnce({ revisions: [{ id: "first", created_at: 2000 }], next_cursor: "first" }).mockResolvedValueOnce({ revisions: [{ id: "older", created_at: 1000 }], next_cursor: null }).mockResolvedValue({ revisions: [{ id: "latest", created_at: 3000 }], next_cursor: null });
  const open = vi.fn(); render(<ContextPanel session={session} onMemory={open} />, { wrapper: edition(FULL) });
  fireEvent.click(await screen.findByRole("button", { name: "Older memories" }));
  await screen.findAllByRole("button", { name: "View memory ↗" });
  await act(async () => {});
  const buttons = screen.getAllByRole("button", { name: "View memory ↗" }); expect(buttons).toHaveLength(2);
  fireEvent.click(buttons[1]); expect(open).toHaveBeenLastCalledWith("older");
  act(() => { window.dispatchEvent(new CustomEvent("duckterm-checkpoint", { detail: session.key })); });
  await act(async () => {});
  fireEvent.click(screen.getByRole("button", { name: "View memory ↗" })); expect(open).toHaveBeenLastCalledWith("latest");
});
it("does not invent capacity or compaction from model name or lifetime events", async () => {
  render(<ContextPanel session={{ ...session, eventCount: 40000, contextTokens: 334881, model: "claude-opus-5" }} />, { wrapper: edition(FULL) });
  await screen.findByText("No saved memories yet.");
  expect(screen.getByLabelText("Context tokens")).toHaveTextContent("334,881 used · — left");
  expect(screen.queryByText(/Compacted/)).toBeNull();
});
it("keeps zero reported usage distinct from missing usage", async () => {
  vi.mocked(api.checkpoints).mockResolvedValue({ checkpoints: [], context_evidence: evidence });
  const view = render(<ContextPanel session={{ ...session, contextTokens: 0 }} />, { wrapper: edition(FULL) });
  await screen.findByText("258,400 left"); expect(screen.getByLabelText("Context tokens")).toHaveTextContent("0 used");
  view.rerender(<ContextPanel session={{ ...session, contextTokens: undefined }} />);
  expect(screen.getByLabelText("Context tokens")).toHaveTextContent("— used · — left");
});
it("shows reported remaining capacity and clears it after host failure", async () => {
  vi.mocked(api.checkpoints).mockResolvedValueOnce({ checkpoints: [], context_evidence: evidence }).mockRejectedValue(new Error("offline"));
  render(<ContextPanel session={session} />, { wrapper: edition(FULL) });
  await screen.findByText("68,400 left");
  expect(document.querySelector("time")).toHaveAttribute("datetime", evidence.last_compaction_at);
  act(() => { window.dispatchEvent(new CustomEvent("duckterm-checkpoint", { detail: session.key })); });
  await screen.findByTitle("Couldn’t load context"); expect(screen.queryByText("68,400 left")).toBeNull();
});
it("ignores late memory and context from a previously selected host/session", async () => {
  let finish!: (value: Awaited<ReturnType<typeof api.checkpoints>>) => void;
  let finishMemory!: (value: unknown) => void;
  vi.mocked(api.checkpoints).mockImplementationOnce(() => new Promise(resolve => { finish = resolve; })).mockResolvedValue({ checkpoints: [] });
  vi.mocked(memoryGet).mockImplementationOnce(() => new Promise(resolve => { finishMemory = resolve; })).mockResolvedValue({ revisions: [], next_cursor: null });
  const view = render(<ContextPanel session={session} />, { wrapper: edition(FULL) });
  view.rerender(<ContextPanel session={{ ...session, key: "remote:two" }} />);
  await act(async () => { finish({ checkpoints: [cp], context_evidence: evidence }); finishMemory({ revisions: [{ id: "wrong-host", created_at: 1 }], next_cursor: null }); });
  expect(screen.queryByRole("button", { name: "View memory ↗" })).toBeNull(); expect(screen.queryByText("68,400 left")).toBeNull();
});
it("retains a compact failure link and sends compact only on request", async () => {
  const open = vi.fn(); render(<ContextPanel session={session} onCheckpointMemory={open} />, { wrapper: edition(FULL) });
  fireEvent.click(await screen.findByRole("button", { name: /Latest checkpoint/ })); expect(open).toHaveBeenCalledWith("c");
  const activeContext = screen.getByText("Active context").closest("details")!;
  fireEvent.click(within(activeContext).getByRole("button", { name: "Compact" }));
  await act(async () => {}); expect(api.sendInput).toHaveBeenCalledWith("one", "/compact\n");
});
