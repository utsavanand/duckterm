import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { memorySwitchAvailable, memorySwitchService } from "./memorySwitchTransport";
import { initialMemorySwitch, memorySwitchReducer, type SwitchSelection, type SwitchSubmission } from "./memorySwitchState";
import { sessionRef } from "./hostTransport";
import type { RestartOptions } from "./api";

vi.mock("./api", () => ({ authHeaders: (extra = {}) => ({ "X-Duckterm-Token": "test-owner", ...extra }) }));
const selection: SwitchSelection = { sessionRef: "same.id", sourceGeneration: "generation-a",
  sourceHarness: "claude-code", harness: "codex", model: { mode: "explicit", id: "gpt-6-astra" } };
const binding = { session_key: "same.id", source_generation: "generation-a",
  target: { harness: "codex", model: { mode: "explicit", id: "gpt-6-astra" } } };
const prepId = "a".repeat(32);
const snapshotId = "b".repeat(64);
const prepared = {
  version: 1, preparation_id: prepId, request_key: "another-dialog-lease", sequence: 4,
  binding, state: "ready", coverage: { state: "partial", available_text: "processed", retrieval: "available",
    retention: "retained_snapshot", source_count: 3, covered_source_count: 3, gap_count: 1,
    gaps: [{ source_id: "source1", kind: "unread_attachment", reason: "Image not interpreted", blocking: false }],
    has_more: false, details_cursor: null },
  proof: { snapshot_id: snapshotId, revision_id: "revision1", prepared_at: 1000, expires_at: 2000,
    resolved_model: "gpt-6-astra", overview: "Continue this work." },
};
const submission: SwitchSubmission = { scope: { selection, controllerId: "dialog1", attempt: 1 },
  requestKey: "switch:1", interrupt: false, proof: { selection, preparationId: prepId, snapshotId, expiresAt: 2000 } };
const receipt = { id: "op1", request_key: "switch:1", sequence: 1, binding, preparation_id: prepId,
  status: "queued", can_cancel: true, process_state: "source_running", error: null, code: null,
  target_generation: null, configured_model: null };
const brief = "Keep the café notes 🦆";
const details = { preparation_id: prepId, snapshot_id: snapshotId,
  brief: { text: brief, utf8_bytes: new TextEncoder().encode(brief).length, budget_bytes: 32000 },
  sources: [{ source_id: "source1", source_version: "c".repeat(64), read_handle: `source1:${"c".repeat(64)}` }],
  gaps: [], next_cursor: "50" };
const fetchMock = vi.fn();
function response(value: unknown, status = 200) { fetchMock.mockResolvedValueOnce(new Response(JSON.stringify(value), { status })); }
beforeEach(() => { vi.stubGlobal("fetch", fetchMock); });
afterEach(() => { vi.unstubAllGlobals(); vi.clearAllMocks(); });

it("starts preparation with exact binding and auth, without starting a restart", async () => {
  response({ ...prepared, state: "preparing", phase: "reading", sequence: 0 }, 202);
  const service = memorySwitchService(selection);
  const value = await service.prepare("dialog:1");
  expect(value.result.phase).toBe("preparing");
  const [path, init] = fetchMock.mock.calls[0];
  expect(path).toBe("/sessions/same.id/restart-preparation");
  expect(init.headers).toMatchObject({ "X-Duckterm-Token": "test-owner", "Content-Type": "application/json" });
  expect(JSON.parse(init.body)).toEqual({ request_key: "dialog:1", binding });
  expect(fetchMock).toHaveBeenCalledTimes(1);
});
it("accepts a shared-job poll with a different lease and maps readiness into state", async () => {
  response(prepared);
  const value = await memorySwitchService(selection).preparation(prepId);
  const state = memorySwitchReducer(initialMemorySwitch(selection, "dialog1"), { type: "prepare" });
  const next = memorySwitchReducer(state, { type: "prepared", scope: state.scope, sequence: value.sequence, result: value.result });
  expect(next.phase).toBe("ready");
  expect(next.proof?.snapshotId).toBe(snapshotId);
  expect(value.coverage.gaps[0].blocking).toBe(false);
  expect(fetchMock.mock.calls[0][1].headers).toMatchObject({ "X-Duckterm-Token": "test-owner" });
});
it.each([
  { version: 2 }, { preparation_id: "other" },
  { binding: { ...binding, session_key: "other" } },
  { binding: { ...binding, source_generation: "generation-b" } },
  { binding: { ...binding, target: { ...binding.target, model: { mode: "default" } } } },
  { coverage: { ...prepared.coverage, available_text: "not_processed" } },
  { coverage: { ...prepared.coverage, gaps: [{ ...prepared.coverage.gaps[0], blocking: true }] } },
])("rejects inconsistent preparation response %j", async patch => {
  response({ ...prepared, ...patch });
  await expect(memorySwitchService(selection).preparation(prepId)).rejects.toMatchObject({ code: "invalid_response" });
});
it.each(["incomplete_source", "stale_source", "failed", "canceled"])("preserves preparation state %s", async state => {
  response({ ...prepared, state, reason: "Source changed", retryable: state === "failed" });
  expect((await memorySwitchService(selection).preparation(prepId)).result.phase).toBe(state);
});
it("reads full Unicode brief and pages exact source handles bound to the snapshot", async () => {
  response(details);
  const data = await memorySwitchService(selection).details(prepId, snapshotId, "50");
  expect(data.brief.text).toBe(brief);
  expect(data.sources).toEqual(details.sources);
  expect(data.nextCursor).toBe("50");
  expect(fetchMock.mock.calls[0][0]).toBe(`/sessions/same.id/restart-preparation/${prepId}?detail=full&cursor=50`);
});
it.each([
  { snapshot_id: "stale" }, { brief: { ...details.brief, utf8_bytes: brief.length } },
  { brief: { ...details.brief, budget_bytes: 5 } },
  { sources: [{ ...details.sources[0], read_handle: "/tmp/arbitrary" }] },
])("rejects incomplete or wrongly bound detail %j", async patch => {
  response({ ...details, ...patch });
  await expect(memorySwitchService(selection).details(prepId, snapshotId)).rejects.toMatchObject({ code: "invalid_response" });
});
it("releases only the dialog lease and never calls restart cancellation", async () => {
  response({ released: true });
  await memorySwitchService(selection).release(prepId, "dialog:1");
  expect(fetchMock).toHaveBeenCalledWith(`/sessions/same.id/restart-preparation/${prepId}?request_key=dialog%3A1`, expect.objectContaining({ method: "DELETE" }));
  expect(fetchMock).toHaveBeenCalledTimes(1);
});
it("uses existing restart with proof and idempotency key only on explicit switch", async () => {
  response(receipt, 202);
  const result = await memorySwitchService(selection).switch(submission);
  expect(result.phase).toBe("queued");
  expect(fetchMock.mock.calls[0][0]).toBe("/sessions/same.id/restart");
  expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual({ harness: "codex", model: "gpt-6-astra",
    interrupt: false, request_key: "switch:1", memory: { version: 1, preparation_id: prepId,
      snapshot_id: snapshotId, source_generation: "generation-a" } });
});
it("reconciles a lost response using the original key and does not retry POST", async () => {
  fetchMock.mockRejectedValueOnce(new TypeError("Network lost"));
  const service = memorySwitchService(selection);
  await expect(service.switch(submission)).rejects.toThrow("Network lost");
  response({ ...receipt, status: "restarting", can_cancel: false, sequence: 2, process_state: "source_stopped" });
  const value = await service.operation(submission);
  expect(value).toMatchObject({ phase: "switching", processState: "source_stopped" });
  expect(fetchMock.mock.calls.map(c => c[1].method)).toEqual(["POST", "GET"]);
  expect(fetchMock.mock.calls[1][0]).toBe("/sessions/same.id/restart?request_key=switch%3A1");
});
it("cancels the exact queued operation and returns acknowledged status", async () => {
  response({ ...receipt, status: "canceled", can_cancel: false });
  expect((await memorySwitchService(selection).cancel(submission, "op1")).phase).toBe("canceled");
  expect(fetchMock.mock.calls[0][0]).toBe("/sessions/same.id/restart?operation_id=op1");
});
it.each([{ request_key: "other" }, { preparation_id: "other" }, { binding: { ...binding, source_generation: "other" } }])(
  "rejects an operation that belongs to a different request/proof %j", async patch => {
    response({ ...receipt, ...patch });
    await expect(memorySwitchService(selection).operation(submission)).rejects.toMatchObject({ code: "invalid_response" });
  });
it("preserves structured failure and unknown process state instead of parsing text", async () => {
  response({ error: "The original process was stopped", code: "launch_failed", process_state: "source_stopped", retryable: false }, 409);
  await expect(memorySwitchService(selection).switch(submission)).rejects.toMatchObject({ status: 409, code: "launch_failed", processState: "source_stopped", retryable: false });
  response({ error: "Not found", code: "preparation_expired" }, 409);
  await expect(memorySwitchService(selection).preparation(prepId)).rejects.toMatchObject({ processState: "unknown", code: "preparation_expired" });
});
it("preserves server-reported target generation and an unreported configured model", async () => {
  response({ ...receipt, status: "completed", can_cancel: false, process_state: "target_running", target_generation: "generation-b" });
  expect(await memorySwitchService(selection).operation(submission)).toMatchObject({ phase: "completed", targetGeneration: "generation-b", configuredModel: null });
});
it("does not issue remote memory requests or malformed paths", async () => {
  const remote = memorySwitchService({ ...selection, sessionRef: sessionRef("remote-host", "same.id") });
  await expect(remote.prepare("dialog1")).rejects.toMatchObject({ code: "unsupported" });
  await expect(memorySwitchService(selection).preparation("../escape")).rejects.toMatchObject({ code: "invalid_request" });
  expect(fetchMock).not.toHaveBeenCalled();
});
it("passes abort signals to local reads and treats absent or unknown capability as unavailable", async () => {
  const controller = new AbortController(); response(prepared);
  await memorySwitchService(selection).preparation(prepId, controller.signal);
  expect(fetchMock.mock.calls[0][1].signal).toBe(controller.signal);
  const options = { current: { harness: "claude-code", model: "", conversation_generation: "gen" },
    memory_switch: { version: 1, available: true }, resume_restart: { available: true }, harnesses: [] } satisfies RestartOptions;
  expect(memorySwitchAvailable(options)).toBe(true);
  expect(memorySwitchAvailable({ ...options, memory_switch: { version: 2, available: true } })).toBe(false);
  expect(memorySwitchAvailable({ ...options, memory_switch: undefined })).toBe(false);
  expect(memorySwitchAvailable({ ...options, current: { harness: "claude-code", model: "" } })).toBe(false);
});
