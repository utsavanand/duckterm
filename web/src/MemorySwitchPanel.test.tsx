import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { MemorySwitchPanel } from "./MemorySwitchPanel";
import { memorySwitchService, MemoryTransportError, type MemoryPreparation } from "./memorySwitchTransport";
import type { SwitchSelection } from "./memorySwitchState";
vi.mock("./memorySwitchTransport", async original => ({ ...await original<typeof import("./memorySwitchTransport")>(), memorySwitchService: vi.fn() }));
const selection: SwitchSelection = { sessionRef: "test", sourceHarness: "claude-code", sourceGeneration: "a", harness: "codex", model: { mode: "explicit", id: "gpt-6-astra" } };
const service = { prepare: vi.fn(), preparation: vi.fn(), details: vi.fn(), release: vi.fn(), switch: vi.fn(), operation: vi.fn(), cancel: vi.fn() };
const ready = (): MemoryPreparation => ({ preparationId: "p1", sequence: 1, result: { phase: "ready", proof: { selection, preparationId: "p1", snapshotId: "s1", expiresAt: Date.now() + 60000 } },
  coverage: { state: "complete", available_text: "processed", retrieval: "available", retention: "retained_snapshot", source_count: 3, covered_source_count: 3, gap_count: 0, gaps: [], has_more: false, details_cursor: null }, resolvedModel: "gpt-6-astra" });
const props = () => ({ selection, targetName: "Codex", allowed: true, afterTurn: true, canInterrupt: true, close: vi.fn(), onBusy: vi.fn(), onAccepted: vi.fn() });
beforeEach(() => {
  Object.defineProperty(document, "visibilityState", { configurable: true, value: "visible" });
  vi.mocked(memorySwitchService).mockReturnValue(service);
  service.prepare.mockResolvedValue(ready()); service.preparation.mockImplementation(async () => ready());
  service.release.mockResolvedValue(undefined);
  service.switch.mockImplementation(async s => ({ id: "op1", requestKey: s.requestKey, sequence: 1, phase: "queued", processState: "source_running" }));
});
afterEach(() => { cleanup(); vi.resetAllMocks(); vi.useRealTimers(); });
it("shows a terminal preparation failure even if the window remains hidden", async () => {
  vi.useFakeTimers();
  Object.defineProperty(document, "visibilityState", { configurable: true, value: "hidden" });
  service.prepare.mockResolvedValue({ ...ready(), sequence: 0, result: { phase: "preparing" } });
  service.preparation.mockResolvedValue({ ...ready(), sequence: 1,
    result: { phase: "failed", reason: "History batch timed out" } });
  await act(async () => { render(<MemorySwitchPanel {...props()} />); });
  expect(screen.getByText("Preparing the handoff…")).toBeInTheDocument();
  await act(async () => { await vi.advanceTimersByTimeAsync(2000); });
  expect(screen.getByText("History batch timed out")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Prepare again" })).toBeEnabled();
  expect(screen.getByRole("button", { name: "Switch to Codex" })).toBeDisabled();
  await act(async () => { await vi.advanceTimersByTimeAsync(30000); });
  expect(service.preparation).toHaveBeenCalledOnce();
  expect(service.switch).not.toHaveBeenCalled();
});
it("throttles background preparation reads and stops them when the dialog closes", async () => {
  vi.useFakeTimers();
  Object.defineProperty(document, "visibilityState", { configurable: true, value: "hidden" });
  const preparing = { ...ready(), sequence: 0, result: { phase: "preparing" as const } };
  service.prepare.mockResolvedValue(preparing); service.preparation.mockResolvedValue(preparing);
  let view!: ReturnType<typeof render>;
  await act(async () => { view = render(<MemorySwitchPanel {...props()} />); });
  await act(async () => { await vi.advanceTimersByTimeAsync(2000); });
  expect(service.preparation).toHaveBeenCalledOnce();
  await act(async () => { await vi.advanceTimersByTimeAsync(9999); });
  expect(service.preparation).toHaveBeenCalledOnce();
  await act(async () => { await vi.advanceTimersByTimeAsync(1); });
  expect(service.preparation).toHaveBeenCalledTimes(2);
  view.unmount();
  await act(async () => { await vi.advanceTimersByTimeAsync(30000); });
  expect(service.preparation).toHaveBeenCalledTimes(2);
  expect(service.release).toHaveBeenCalledWith("p1", expect.any(String));
});
it("prepares automatically but requires explicit Switch and prevents double submission", async () => {
  const p = props(); render(<MemorySwitchPanel {...p} />);
  await screen.findByText("Ready to switch"); expect(service.switch).not.toHaveBeenCalled();
  const button = screen.getByRole("button", { name: "Switch to Codex" });
  fireEvent.click(button); fireEvent.click(button);
  await waitFor(() => expect(p.onAccepted).toHaveBeenCalledOnce());
  expect(service.switch).toHaveBeenCalledOnce(); expect(service.switch.mock.calls[0][0].interrupt).toBe(false);
});
it("requires explicit interrupt consent and retains the exact model selection", async () => {
  render(<MemorySwitchPanel {...props()} />); await screen.findByText("Ready to switch");
  expect(screen.getByRole("checkbox")).not.toBeChecked(); fireEvent.click(screen.getByRole("checkbox"));
  fireEvent.click(screen.getByRole("button", { name: "Stop and switch now" }));
  await waitFor(() => expect(service.switch).toHaveBeenCalledOnce());
  expect(service.switch.mock.calls[0][0]).toMatchObject({ interrupt: true, scope: { selection } });
});
it("releases a late preparation lease after close without switching or canceling restart", async () => {
  let finish!: (p: MemoryPreparation) => void;
  service.prepare.mockImplementationOnce(() => new Promise(resolve => { finish = resolve; }));
  const view = render(<MemorySwitchPanel {...props()} />); view.unmount();
  await act(async () => finish(ready()));
  expect(service.release).toHaveBeenCalledWith("p1", expect.any(String));
  expect(service.switch).not.toHaveBeenCalled(); expect(service.cancel).not.toHaveBeenCalled();
});
it("disables Switch for missing sources and retries with a fresh lease", async () => {
  service.prepare.mockResolvedValueOnce({ ...ready(), result: { phase: "incomplete_source", reason: "Missing original history" } });
  render(<MemorySwitchPanel {...props()} />);
  await screen.findByText("Missing original history"); expect(screen.getByRole("button", { name: "Switch to Codex" })).toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: "Prepare again" }));
  await screen.findByText("Ready to switch"); expect(service.prepare).toHaveBeenCalledTimes(2);
  expect(service.prepare.mock.calls[0][0]).not.toBe(service.prepare.mock.calls[1][0]);
});
it("replays the same preparation key when its initial response is lost", async () => {
  service.prepare.mockRejectedValueOnce(new TypeError("Network lost"));
  render(<MemorySwitchPanel {...props()} />); await screen.findByText("Network lost");
  fireEvent.click(screen.getByRole("button", { name: "Prepare again" })); await screen.findByText("Ready to switch");
  expect(service.prepare.mock.calls[0][0]).toBe(service.prepare.mock.calls[1][0]);
});
it("loads the optional complete brief only on expansion", async () => {
  const text = "Earlier constraint. ".repeat(1000);
  service.details.mockResolvedValue({ preparationId: "p1", snapshotId: "s1", brief: { text, utf8_bytes: text.length, budget_bytes: 32000 }, sources: [], gaps: [], nextCursor: null });
  render(<MemorySwitchPanel {...props()} />); await screen.findByText("Ready to switch");
  expect(service.details).not.toHaveBeenCalled();
  const summary = screen.getByText("Handoff brief and available context");
  summary.parentElement!.setAttribute("open", ""); fireEvent(summary.parentElement!, new Event("toggle"));
  expect((await screen.findByRole("region", { name: "Automatically prepared brief" })).textContent).toBe(text);
  expect(service.switch).not.toHaveBeenCalled();
});
it("does not let a ready proof bypass a newly blocked terminal draft", async () => {
  const p = props(); const view = render(<MemorySwitchPanel {...p} />); await screen.findByText("Ready to switch");
  view.rerender(<MemorySwitchPanel {...p} allowed={false} reason="Send or clear your draft" />);
  expect(screen.getByRole("button", { name: "Switch to Codex" })).toBeDisabled(); expect(service.switch).not.toHaveBeenCalled();
});
it("keeps a rejected final switch in the dialog with retry and cleared interrupt", async () => {
  service.switch.mockRejectedValueOnce(new MemoryTransportError("Sources changed", 409, "stale_source", "source_running"));
  const p = props(); render(<MemorySwitchPanel {...p} />); await screen.findByText("Ready to switch");
  fireEvent.click(screen.getByRole("checkbox")); fireEvent.click(screen.getByRole("button", { name: "Stop and switch now" }));
  await screen.findByText("Sources changed"); expect(p.onAccepted).not.toHaveBeenCalled();
  expect(screen.getByRole("checkbox")).not.toBeChecked(); expect(screen.getByRole("button", { name: "Prepare again" })).toBeEnabled();
});
it("reconciles a lost Switch response without another automatic POST", async () => {
  service.switch.mockRejectedValueOnce(new TypeError("Lost"));
  service.operation.mockImplementation(async s => ({ id: "op1", requestKey: s.requestKey, sequence: 1, phase: "queued", processState: "source_running" }));
  const p = props(); render(<MemorySwitchPanel {...p} />); await screen.findByText("Ready to switch");
  fireEvent.click(screen.getByRole("button", { name: "Switch to Codex" })); await waitFor(() => expect(p.onAccepted).toHaveBeenCalledOnce());
  expect(service.switch).toHaveBeenCalledOnce(); expect(service.operation.mock.calls[0][0]).toBe(service.switch.mock.calls[0][0]);
});
it("explicitly checks an unrecorded switch by replaying the original immutable request", async () => {
  service.switch.mockRejectedValueOnce(new TypeError("Lost"));
  service.operation.mockRejectedValue(new MemoryTransportError("No receipt", 404, "operation_conflict"));
  const p = props(); render(<MemorySwitchPanel {...p} />); await screen.findByText("Ready to switch");
  fireEvent.click(screen.getByRole("button", { name: "Switch to Codex" })); await screen.findByText(/No receipt/);
  expect(service.switch).toHaveBeenCalledOnce(); fireEvent.click(screen.getByRole("button", { name: "Check switch status" }));
  await waitFor(() => expect(p.onAccepted).toHaveBeenCalledOnce());
  expect(service.switch.mock.calls[1][0]).toBe(service.switch.mock.calls[0][0]);
});
