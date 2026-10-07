import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { MemorySwitchPanel } from "./MemorySwitchPanel";
import type { SwitchSelection } from "./memorySwitchState";

// Exercise the actual transport decoder and controller together. Mock only HTTP.
vi.mock("./api", () => ({ authHeaders: (extra = {}) => ({ "X-Duckterm-Token": "test-owner", ...extra }) }));
const selection: SwitchSelection = { sessionRef: "test", sourceHarness: "claude-code", sourceGeneration: "a", harness: "codex", model: { mode: "explicit", id: "gpt-6-astra" } };
const binding = { session_key: "test", source_generation: "a", target: { harness: "codex", model: selection.model } };
const fetchMock = vi.fn();
const props = () => ({ selection, targetName: "Codex", allowed: true, afterTurn: false, canInterrupt: false, close: vi.fn(), onBusy: vi.fn(), onAccepted: vi.fn() });
function packet(saved = true) {
  return { version: 1, preparation_id: "p1", sequence: 1, binding, state: "ready",
    coverage: { state: "complete", available_text: saved ? "partial" : "not_processed", retrieval: "available", retention: "retained_snapshot",
      source_count: 3, covered_source_count: 3, gap_count: 0, gaps: [], has_more: false, details_cursor: null,
      handoff: { method: "maintained", summary_revision_id: saved ? "r1" : null, summary_generated_at: saved ? 1791410000000 : null,
        summary_state: saved ? "partial" : "unavailable", available_records: 500, summarized_records: saved ? 120 : 0, included_records: 8, omitted_records: saved ? 372 : 492 } },
    proof: { snapshot_id: "s1", revision_id: saved ? "r1" : null, prepared_at: Date.now(), expires_at: Date.now() + 60000, resolved_model: "gpt-6-astra", overview: "Continue current work." } };
}
function respond(value: unknown, status = 200) { return new Response(JSON.stringify(value), { status }); }
function serve(value = packet()) {
  fetchMock.mockImplementation(async (path: string, init: RequestInit) => {
    if (init.method === "DELETE") return respond({ released: true });
    if (path.includes("detail=full")) return respond({ preparation_id: "p1", snapshot_id: "s1", brief: { text: "Exact saved brief", utf8_bytes: 17, budget_bytes: 32000 }, sources: [], gaps: [], next_cursor: null });
    if (path.endsWith("/restart")) {
      const body = JSON.parse(init.body as string);
      return respond({ id: "op1", request_key: body.request_key, preparation_id: "p1", sequence: 1, binding, status: "queued", can_cancel: true, process_state: "source_running" });
    }
    return respond(value);
  });
}
function switches() { return fetchMock.mock.calls.filter(([path, init]) => path.endsWith("/restart") && init.method === "POST"); }
beforeEach(() => { vi.stubGlobal("fetch", fetchMock); serve(); });
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.clearAllMocks(); });

it.each([true, false])("accepts maintained readiness through HTTP with saved summary=%s without automatically switching", async saved => {
  serve(packet(saved)); const p = props(); render(<MemorySwitchPanel {...p} />);
  await screen.findByText("Ready to switch");
  expect(screen.getByText(saved ? "The brief uses saved memory and recent messages. Earlier history stays searchable." : "No saved summary yet. The brief uses current work and recent messages; earlier history stays searchable.")).toBeVisible();
  expect(switches()).toHaveLength(0);
  const summary = screen.getByText("Handoff brief and available context");
  summary.parentElement!.setAttribute("open", ""); fireEvent(summary.parentElement!, new Event("toggle"));
  expect(screen.getByText(saved ? "120 of 500 records" : "0 of 500 records")).toBeVisible();
  expect(screen.getByText(saved ? "372 records available through read tools" : "492 records available through read tools")).toBeVisible();
  expect(screen.getByText("Retained and searchable")).toBeVisible();
  if (!saved) expect(screen.getByText("No saved summary")).toBeVisible();
  expect(await screen.findByRole("region", { name: "Automatically prepared brief" })).toHaveTextContent("Exact saved brief");
  const button = screen.getByRole("button", { name: "Switch to Codex" });
  fireEvent.click(button); fireEvent.click(button);
  await waitFor(() => expect(p.onAccepted).toHaveBeenCalledOnce());
  expect(switches()).toHaveLength(1);
  expect(JSON.parse(switches()[0][1].body)).toMatchObject({ harness: "codex", model: "gpt-6-astra", interrupt: false,
    memory: { version: 1, preparation_id: "p1", snapshot_id: "s1", source_generation: "a" } });
});
it("keeps the final draft guard even with a no-summary maintained proof", async () => {
  serve(packet(false)); const p = props(); const view = render(<MemorySwitchPanel {...p} />);
  await screen.findByText("Ready to switch");
  view.rerender(<MemorySwitchPanel {...p} allowed={false} reason="Send or clear your draft" />);
  const button = screen.getByRole("button", { name: "Switch to Codex" });
  expect(button).toBeDisabled(); fireEvent.click(button); expect(switches()).toHaveLength(0);
});
it("rejects a no-summary proof when exact originals are unavailable", async () => {
  const value = packet(false); value.coverage.retrieval = "partial"; serve(value);
  render(<MemorySwitchPanel {...props()} />);
  await screen.findByRole("alert");
  expect(screen.getByRole("button", { name: "Switch to Codex" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "Prepare again" })).toBeEnabled(); expect(switches()).toHaveLength(0);
});
it("reports a real source change before any switch POST", async () => {
  const value = { ...packet(false), state: "stale_source", reason: "Current work changed; prepare again" }; serve(value);
  render(<MemorySwitchPanel {...props()} />);
  await screen.findByText("Work changed during preparation");
  expect(screen.getByRole("button", { name: "Switch to Codex" })).toBeDisabled(); expect(switches()).toHaveLength(0);
});
