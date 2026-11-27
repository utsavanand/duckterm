import { act, cleanup, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { useSessionResource } from "./useSessionResource";
import { routedFetch, sessionRef } from "./hostTransport";
vi.mock("./hostTransport", async original => ({ ...await original<typeof import("./hostTransport")>(), routedFetch: vi.fn() }));
let index = 0;
const nextKey = () => `resource-test-${++index}`;
const response = (messages: unknown[]) => ({ ok: true, json: async () => ({ messages }) }) as Response;
beforeEach(() => { vi.useFakeTimers(); vi.mocked(routedFetch).mockReset(); vi.spyOn(document, "visibilityState", "get").mockReturnValue("visible"); });
afterEach(() => { cleanup(); vi.useRealTimers(); vi.restoreAllMocks(); });
const flush = () => act(async () => { await Promise.resolve(); });
it("shows the last reply immediately after remount and retains it on refresh errors", async () => {
  const key = nextKey(); vi.mocked(routedFetch).mockResolvedValue(response(["last reply"]));
  const first = renderHook(() => useSessionResource<string>(key, "messages", true)); await flush();
  expect(first.result.current.items).toEqual(["last reply"]); first.unmount();
  vi.mocked(routedFetch).mockRejectedValue(new Error("offline"));
  const reopened = renderHook(() => useSessionResource<string>(key, "messages", true));
  expect(reopened.result.current.items).toEqual(["last reply"]); await flush();
  expect(reopened.result.current).toMatchObject({ items: ["last reply"], error: true });
});
it("never overlaps slow reads, including remount while a read is pending", async () => {
  const key = nextKey(); let finish!: (response: Response) => void;
  vi.mocked(routedFetch).mockImplementation(() => new Promise(resolve => { finish = resolve; }));
  const first = renderHook(() => useSessionResource<string>(key, "messages", true));
  await act(async () => { await vi.advanceTimersByTimeAsync(12000); });
  expect(routedFetch).toHaveBeenCalledTimes(1); first.unmount();
  const second = renderHook(() => useSessionResource<string>(key, "messages", true)); expect(routedFetch).toHaveBeenCalledTimes(1);
  await act(async () => { finish(response(["complete"])); }); expect(second.result.current.items).toEqual(["complete"]);
  await act(async () => { await vi.advanceTimersByTimeAsync(2999); }); expect(routedFetch).toHaveBeenCalledTimes(1);
  await act(async () => { await vi.advanceTimersByTimeAsync(1); }); expect(routedFetch).toHaveBeenCalledTimes(2);
});
it("pauses while covered or document-hidden and refreshes on return", async () => {
  const key = nextKey(); vi.mocked(routedFetch).mockResolvedValue(response(["reply"]));
  const view = renderHook(({ active }) => useSessionResource<string>(key, "messages", active), { initialProps: { active: true } }); await flush();
  view.rerender({ active: false }); await act(async () => { await vi.advanceTimersByTimeAsync(12000); }); expect(routedFetch).toHaveBeenCalledTimes(1);
  view.rerender({ active: true }); await flush(); expect(routedFetch).toHaveBeenCalledTimes(2);
  vi.spyOn(document, "visibilityState", "get").mockReturnValue("hidden"); act(() => document.dispatchEvent(new Event("visibilitychange")));
  await act(async () => { await vi.advanceTimersByTimeAsync(12000); }); expect(routedFetch).toHaveBeenCalledTimes(2);
  vi.spyOn(document, "visibilityState", "get").mockReturnValue("visible"); act(() => document.dispatchEvent(new Event("visibilitychange"))); await flush(); expect(routedFetch).toHaveBeenCalledTimes(3);
});
it("isolates equal session IDs on different hosts and ignores old responses", async () => {
  const key = nextKey(); let finish!: (response: Response) => void;
  vi.mocked(routedFetch).mockImplementationOnce(() => new Promise(resolve => { finish = resolve; })).mockResolvedValue(response(["remote reply"]));
  const view = renderHook(({ ref }) => useSessionResource<string>(ref, "messages", true), { initialProps: { ref: key } });
  view.rerender({ ref: sessionRef("remote", key) }); expect(view.result.current.items).toBeUndefined(); await flush();
  await act(async () => { finish(response(["local reply"])); }); expect(view.result.current.items).toEqual(["remote reply"]);
  view.rerender({ ref: key }); expect(view.result.current.items).toEqual(["local reply"]);
});
it("reuses unchanged data, accepts an empty list and clears inaccessible content", async () => {
  const key = nextKey(); vi.mocked(routedFetch).mockResolvedValue(response(["reply"]));
  const view = renderHook(() => useSessionResource<string>(key, "messages", true)); await flush(); const original = view.result.current.items;
  await act(async () => { await vi.advanceTimersByTimeAsync(3000); }); expect(view.result.current.items).toBe(original);
  vi.mocked(routedFetch).mockResolvedValue(response([])); await act(async () => { await vi.advanceTimersByTimeAsync(3000); }); expect(view.result.current.items).toEqual([]);
  vi.mocked(routedFetch).mockResolvedValue({ ok: false, status: 403 } as Response); await act(async () => { await vi.advanceTimersByTimeAsync(3000); }); expect(view.result.current).toMatchObject({ items: undefined, error: true });
});
it("fetches again after a pre-save annotation read before displaying the saved comment", async () => {
  const key = nextKey(); let finish!: (response: Response) => void;
  vi.mocked(routedFetch).mockImplementationOnce(() => new Promise(resolve => { finish = resolve; })).mockResolvedValue({ ok: true, json: async () => ({ annotations: ["saved"] }) } as Response);
  const view = renderHook(({ revision }) => useSessionResource<string>(key, "annotations", true, revision), { initialProps: { revision: 0 } });
  view.rerender({ revision: 1 }); expect(routedFetch).toHaveBeenCalledTimes(1);
  await act(async () => { finish({ ok: true, json: async () => ({ annotations: [] }) } as Response); });
  expect(routedFetch).toHaveBeenCalledTimes(2); expect(view.result.current.items).toEqual(["saved"]);
});

it("retains unavailable metadata on remount and detects status-only changes and recovery", async () => {
  const key = nextKey();
  const unavailable = (status: string, reason: string) => ({ ok: true, json: async () => ({ messages: [], transcript: { status, reason } }) }) as Response;
  vi.mocked(routedFetch).mockResolvedValue(unavailable("identity_missing", "No ID recorded"));
  const first = renderHook(() => useSessionResource<string>(key, "messages", true)); await flush();
  expect(first.result.current.transcript).toEqual({ status: "identity_missing", reason: "No ID recorded" });
  first.unmount();
  const reopened = renderHook(() => useSessionResource<string>(key, "messages", true));
  expect(reopened.result.current.transcript?.status).toBe("identity_missing"); await flush();
  vi.mocked(routedFetch).mockResolvedValue(unavailable("not_found", "Missing on this machine"));
  await act(async () => { await vi.advanceTimersByTimeAsync(3000); });
  expect(reopened.result.current.transcript).toEqual({ status: "not_found", reason: "Missing on this machine" });
  vi.mocked(routedFetch).mockResolvedValue(unavailable("not_found", "New explanation"));
  await act(async () => { await vi.advanceTimersByTimeAsync(3000); });
  expect(reopened.result.current.transcript?.reason).toBe("New explanation");
  vi.mocked(routedFetch).mockResolvedValue(response([]));
  await act(async () => { await vi.advanceTimersByTimeAsync(3000); });
  expect(reopened.result.current.items).toEqual([]); expect(reopened.result.current.transcript).toBeUndefined();
  vi.mocked(routedFetch).mockResolvedValue(response(["recovered"]));
  await act(async () => { await vi.advanceTimersByTimeAsync(3000); });
  expect(reopened.result.current.items).toEqual(["recovered"]);
});

// QA reproduced both header and body stalls surviving a tab remount forever.
for (const phase of ["headers", "body"] as const) it(`expires stalled ${phase}, exposes retry state and recovers after remount`, async () => {
  const key = nextKey();
  const never = new Promise<never>(() => {});
  vi.mocked(routedFetch).mockResolvedValueOnce(response(["cached"]));
  const first = renderHook(() => useSessionResource<string>(key, "messages", true)); await flush();
  vi.mocked(routedFetch).mockImplementationOnce(() => phase === "headers" ? never : Promise.resolve(Object.assign(new Response(), { json: () => never }))).mockResolvedValue(response(["recovered"]));
  await act(async () => { await vi.advanceTimersByTimeAsync(3000); });
  const signal = vi.mocked(routedFetch).mock.calls[1][1]?.signal;
  await act(async () => { await vi.advanceTimersByTimeAsync(30000); });
  expect(signal?.aborted).toBe(true);
  expect(first.result.current).toMatchObject({ items: ["cached"], error: true });
  first.unmount();
  const reopened = renderHook(() => useSessionResource<string>(key, "messages", true)); await flush();
  expect(reopened.result.current).toMatchObject({ items: ["recovered"], error: false });
  expect(routedFetch).toHaveBeenCalledTimes(3);
});

it("automatically retries an initial hung read without requiring a remount", async () => {
  vi.mocked(routedFetch).mockImplementationOnce(() => new Promise(() => {})).mockResolvedValue(response(["recovered"]));
  const key = nextKey();
  const view = renderHook(() => useSessionResource<string>(key, "messages", true));
  await act(async () => { await vi.advanceTimersByTimeAsync(30000); });
  expect(view.result.current).toMatchObject({ items: undefined, error: true });
  await act(async () => { await vi.advanceTimersByTimeAsync(3000); });
  expect(view.result.current).toMatchObject({ items: ["recovered"], error: false });
  expect(routedFetch).toHaveBeenCalledTimes(2);
});

for (const late of ["headers", "body", "denied"] as const) it(`ignores expired remote ${late} completion after newer data arrives`, async () => {
  const key = sessionRef("remote", nextKey());
  let finish!: (value: Response) => void;
  let finishBody!: (value: unknown) => void;
  const stalled = new Promise<Response>(resolve => { finish = resolve; });
  const body = new Promise(resolve => { finishBody = resolve; });
  vi.mocked(routedFetch).mockImplementationOnce(() => late === "body" ? Promise.resolve(Object.assign(new Response(), { json: () => body })) : stalled).mockResolvedValue(response(["new"]));
  const view = renderHook(() => useSessionResource<string>(key, "messages", true));
  await act(async () => { await vi.advanceTimersByTimeAsync(33000); });
  expect(view.result.current.items).toEqual(["new"]);
  await act(async () => {
    if (late === "body") finishBody({ messages: ["expired"] });
    else finish(late === "denied" ? { ok: false, status: 403 } as Response : response(["expired"]));
  });
  view.unmount();
  vi.mocked(routedFetch).mockRejectedValue(new Error("offline"));
  const reopened = renderHook(() => useSessionResource<string>(key, "messages", true)); await flush();
  expect(reopened.result.current).toMatchObject({ items: ["new"], error: true });
});

for (const pause of ["inactive", "hidden"] as const) it(`does not retry expired reads while ${pause}`, async () => {
  const key = nextKey();
  vi.mocked(routedFetch).mockImplementationOnce(() => new Promise(() => {})).mockResolvedValue(response(["returned"]));
  const view = renderHook(({ active }) => useSessionResource<string>(key, "messages", active), { initialProps: { active: true } });
  if (pause === "inactive") view.rerender({ active: false });
  else {
    vi.spyOn(document, "visibilityState", "get").mockReturnValue("hidden");
    act(() => document.dispatchEvent(new Event("visibilitychange")));
  }
  await act(async () => { await vi.advanceTimersByTimeAsync(60000); });
  expect(routedFetch).toHaveBeenCalledTimes(1);
  if (pause === "inactive") view.rerender({ active: true });
  else {
    vi.spyOn(document, "visibilityState", "get").mockReturnValue("visible");
    act(() => document.dispatchEvent(new Event("visibilitychange")));
  }
  await flush(); expect(view.result.current.items).toEqual(["returned"]);
});
