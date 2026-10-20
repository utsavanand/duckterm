import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { CheckpointProgress } from "./CheckpointProgress";
import { requestCheckpoint } from "./checkpointRequests";
import { sessionRef } from "./hostTransport";
import type { CheckpointRecord } from "./api";

afterEach(() => { cleanup(); delete window.webkit; delete window.__rubbertermDesktop; vi.unstubAllGlobals(); vi.useRealTimers(); });

it("settles held native bridge POSTs on their owning hosts even after changing the selected host", async () => {
  vi.useFakeTimers();
  const localFetch = vi.fn();
  vi.stubGlobal("fetch", localFetch);
  type ResponseBody = { status: number; body: string };
  type Message = { target: string; operation: string; params: { path: string; method: string; body?: string } };
  const attempts = new Map<string, { id: string; resolve: (value: ResponseBody) => void; reject: (error: Error) => void }>();
  const record = (host: string): CheckpointRecord => ({
    id: `saved-${host}`, request_id: attempts.get(host)!.id, label: "manual", created_at: 1, saved: true, summary: "Fixture summary",
    summary_update: { state: host === "host-a" ? "failed" : "updated", ...(host === "host-a" ? { reason: "summary_unverified" } : {}) },
    record: { prompts: [], files: [], tools: [], event_count: 0 },
  });
  let finishB = false;
  const bridge = vi.fn(async (message: unknown): Promise<ResponseBody> => {
    const { target, operation, params } = message as Message;
    expect(operation).toBe("session-request");
    if (params.method === "POST") {
      expect(params.path).toBe("/sessions/same-session/checkpoint");
      return new Promise((resolve, reject) => { attempts.set(target, { id: JSON.parse(params.body!).request_id, resolve, reject }); });
    }
    expect(params.path).toBe(`/sessions/same-session/checkpoint-attempts/${attempts.get(target)!.id}`);
    const complete = target === "host-a" || finishB;
    return { status: 200, body: JSON.stringify({ request_id: attempts.get(target)!.id,
      state: complete ? "complete" : "running", ...(complete ? { result: record(target) } : { progress: { stage: "writing", completed: 0, total: 20 } }) }) };
  });
  window.webkit = { messageHandlers: { launchRequest: { postMessage: bridge } } };
  window.__rubbertermDesktop = { currentTarget: "host-a", targets: [{ id: "host-a", name: "A" }, { id: "host-b", name: "B" }] };
  const a = sessionRef("host-a", "same-session"), b = sessionRef("host-b", "same-session");
  const view = render(<CheckpointProgress sessionKey={a} />);
  let first!: Promise<CheckpointRecord>, second!: Promise<CheckpointRecord>;
  let firstDone = false, secondDone = false;
  await act(async () => {
    first = requestCheckpoint(a); second = requestCheckpoint(b);
    void first.then(() => { firstDone = true; }); void second.then(() => { secondDone = true; });
  });
  expect(requestCheckpoint(a)).toBe(first); expect(requestCheckpoint(b)).toBe(second);
  window.__rubbertermDesktop.currentTarget = "host-b";
  view.rerender(<CheckpointProgress sessionKey={b} />);
  await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
  expect(firstDone).toBe(true); expect(secondDone).toBe(false);
  expect((await first).summary_update?.state).toBe("failed");
  expect(screen.getByRole("progressbar")).toHaveAttribute("aria-valuenow", "0");
  finishB = true;
  await act(async () => { await vi.advanceTimersByTimeAsync(2000); });
  expect(secondDone).toBe(true);
  expect((await second).id).toBe("saved-host-b");
  expect(screen.getByRole("status")).toHaveTextContent("Summary updated");
  const reads = bridge.mock.calls.length;
  await act(async () => {
    attempts.get("host-a")!.reject(new Error("late bridge disconnect"));
    attempts.get("host-b")!.resolve({ status: 200, body: JSON.stringify(record("host-b")) });
    await vi.advanceTimersByTimeAsync(20_000);
  });
  expect(bridge.mock.calls).toHaveLength(reads);
  expect(bridge.mock.calls.filter(([message]) => (message as Message).params.method === "POST")).toHaveLength(2);
  expect(localFetch).not.toHaveBeenCalled();
  view.rerender(<CheckpointProgress sessionKey={a} />);
  expect(screen.getByRole("status")).toHaveTextContent("Summary update failed");
});
