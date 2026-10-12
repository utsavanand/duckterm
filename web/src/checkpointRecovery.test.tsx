import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { api, type CheckpointRecord, type CheckpointAttemptReceipt } from "./api";
import { requestCheckpoint } from "./checkpointRequests";
import { CheckpointProgress } from "./CheckpointProgress";
import { sessionRef } from "./hostTransport";
vi.mock("./api", () => ({ api: { checkpoint: vi.fn(), checkpointAttempt: vi.fn() } }));
afterEach(() => { cleanup(); vi.resetAllMocks(); vi.useRealTimers(); });
const result = (request_id: string, state: "updated" | "partial" | "failed" = "updated"): CheckpointRecord => ({
  id: "exact-checkpoint", request_id, label: "manual", created_at: 1, saved: true,
  summary: "Saved draft", summary_update: { state }, record: { prompts: [], files: [], tools: [], event_count: 0 },
});
it.each(["updated", "partial", "failed"] as const)("recovers exact remote %s result without another POST, even across panel unmount", async state => {
  vi.useFakeTimers();
  const key = sessionRef("remote-recovery", state);
  vi.mocked(api.checkpoint).mockRejectedValue(new Error("connection lost"));
  let reads = 0;
  vi.mocked(api.checkpointAttempt).mockImplementation(async (_key, id) => ++reads === 1
    ? { request_id: id, state: "running" } : { request_id: id, state: "complete", result: result(id, state) });
  const view = render(<CheckpointProgress sessionKey={key} />);
  let work!: Promise<CheckpointRecord>;
  await act(async () => { work = requestCheckpoint(key); });
  const id = vi.mocked(api.checkpoint).mock.calls[0][2]!;
  expect(id).toMatch(/^[a-f0-9-]{36}$/);
  expect(screen.getByRole("status")).toHaveTextContent("Updating summary");
  expect(requestCheckpoint(key)).toBe(work);
  view.unmount(); render(<CheckpointProgress sessionKey={key} />);
  await act(async () => { await vi.advanceTimersByTimeAsync(5000); await work; });
  expect(screen.getByRole("status")).toHaveTextContent(state === "updated" ? "Summary updated" : state === "partial" ? "Checkpoint failed · summary incomplete" : "Summary update failed");
  expect(api.checkpoint).toHaveBeenCalledExactlyOnceWith(key, "manual", id);
  expect(api.checkpointAttempt).toHaveBeenLastCalledWith(key, id, expect.any(AbortSignal));
});
it.each(["missing", "wrong-receipt", "wrong-result", "failed", "interrupted"])("keeps %s receipts unconfirmed", async kind => {
  const key = `unknown-${kind}`;
  vi.mocked(api.checkpoint).mockRejectedValue(new Error("lost"));
  vi.mocked(api.checkpointAttempt).mockImplementation(async (_key, id) => {
    if (kind === "missing") return null;
    if (kind === "failed" || kind === "interrupted") return { request_id: id, state: kind };
    return { request_id: kind === "wrong-receipt" ? "another-id" : id, state: "complete", result: result("another-id") };
  });
  render(<CheckpointProgress sessionKey={key} />);
  await act(async () => { await expect(requestCheckpoint(key)).rejects.toThrow("lost"); });
  expect(screen.getByRole("status")).toHaveTextContent("Result not confirmed");
  expect(screen.queryByText("Summary updated")).toBeNull();
  expect(api.checkpoint).toHaveBeenCalledOnce();
});
it("keeps confirmed cold-start work running beyond three minutes without another POST", async () => {
  vi.useFakeTimers();
  const key = "long-cold-start";
  vi.mocked(api.checkpoint).mockRejectedValue(new Error("lost"));
  vi.mocked(api.checkpointAttempt).mockImplementation(async (_key, id) => ({ request_id: id, state: "running" }));
  render(<CheckpointProgress sessionKey={key} />);
  let work!: Promise<CheckpointRecord>;
  await act(async () => { work = requestCheckpoint(key); });
  await act(async () => { await vi.advanceTimersByTimeAsync(400_000); });
  expect(screen.getByRole("status")).toHaveTextContent("Updating summary");
  expect(requestCheckpoint(key)).toBe(work);
  expect(api.checkpoint).toHaveBeenCalledOnce();
  vi.mocked(api.checkpointAttempt).mockImplementation(async (_key, id) => ({ request_id: id, state: "complete", result: result(id) }));
  await act(async () => { await vi.advanceTimersByTimeAsync(10_000); await work; });
  expect(screen.getByRole("status")).toHaveTextContent("Summary updated");
});
it("bounds repeated receipt-read failures and releases the failed attempt", async () => {
  vi.useFakeTimers();
  const key = "bounded-unreachable-recovery";
  vi.mocked(api.checkpoint).mockRejectedValue(new Error("lost"));
  vi.mocked(api.checkpointAttempt).mockRejectedValue(new Error("unreachable"));
  render(<CheckpointProgress sessionKey={key} />);
  let work!: Promise<unknown>;
  await act(async () => { work = requestCheckpoint(key).catch(() => null); });
  await act(async () => { await vi.advanceTimersByTimeAsync(180_001); await work; });
  const count = vi.mocked(api.checkpointAttempt).mock.calls.length;
  expect(count).toBeGreaterThan(1); expect(count).toBeLessThanOrEqual(40);
  await vi.advanceTimersByTimeAsync(60_000);
  expect(api.checkpointAttempt).toHaveBeenCalledTimes(count);
  expect(screen.getByRole("status")).toHaveTextContent("Result not confirmed");
});
it("bounds a hung native lookup and ignores its late result after a newer attempt", async () => {
  vi.useFakeTimers();
  const key = "hung-bridge";
  let finish!: (receipt: CheckpointAttemptReceipt) => void;
  vi.mocked(api.checkpoint).mockRejectedValue(new Error("lost"));
  vi.mocked(api.checkpointAttempt).mockImplementation(() => new Promise(resolve => { finish = resolve; }));
  render(<CheckpointProgress sessionKey={key} />);
  let work!: Promise<unknown>;
  await act(async () => { work = requestCheckpoint(key).catch(() => null); });
  const id = vi.mocked(api.checkpoint).mock.calls[0][2]!;
  await act(async () => { await vi.advanceTimersByTimeAsync(10_000); await work; });
  expect(screen.getByRole("status")).toHaveTextContent("Result not confirmed");
  expect(vi.mocked(api.checkpointAttempt).mock.calls[0][2]?.aborted).toBe(true);
  vi.mocked(api.checkpoint).mockImplementation(async (_key, _label, requestId) => result(requestId!, "partial"));
  await act(async () => { await requestCheckpoint(key); finish({ request_id: id, state: "complete", result: result(id) }); });
  expect(screen.getByRole("status")).toHaveTextContent("Checkpoint failed · summary incomplete");
});

it("retries a transient receipt-read error without retrying the checkpoint POST", async () => {
  vi.useFakeTimers();
  const key = "transient-receipt-read";
  vi.mocked(api.checkpoint).mockRejectedValue(new Error("lost POST response"));
  vi.mocked(api.checkpointAttempt).mockRejectedValueOnce(new Error("connection still down"))
    .mockImplementation(async (_key, id) => ({ request_id: id, state: "complete", result: result(id) }));
  render(<CheckpointProgress sessionKey={key} />);
  let work!: Promise<CheckpointRecord>;
  await act(async () => { work = requestCheckpoint(key); });
  await act(async () => { await vi.advanceTimersByTimeAsync(5000); await work; });
  expect(screen.getByRole("status")).toHaveTextContent("Summary updated");
  expect(api.checkpoint).toHaveBeenCalledOnce();
  expect(api.checkpointAttempt).toHaveBeenCalledTimes(2);
});
