import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { api, RelayNote, RelayState } from "./api";
import { POLL_TIMEOUT_MS, useRelay } from "./relay";

vi.mock("./api", () => ({ api: { relay: vi.fn(), relayCount: vi.fn() } }));

const note: RelayNote = {
  id: "n-57fb8c050382", session_key: "ui", name: "ui-dev", folder: "Duckterm", runtime: "codex",
  kind: "choice", status: "open", created_at: 1, questions: [{ question: "Oracle check (B16)?", options: ["Yes", "No"] }],
};
const withNote: RelayState = { notes: [note], rules: [], open: 1 };

beforeEach(() => vi.useFakeTimers());
afterEach(() => { vi.useRealTimers(); vi.resetAllMocks(); });

it("keeps polling after a request that never answers, so a new note still shows", async () => {
  // B16 live miss, 2026-10-01: a note stayed open for 3 hours and the owner
  // never saw it. One hung poll used to stop the loop for good.
  vi.mocked(api.relay).mockReturnValueOnce(new Promise(() => {})).mockResolvedValue(withNote);
  const { result } = renderHook(() => useRelay());
  expect(result.current.notes).toEqual([]);
  await act(async () => { await vi.advanceTimersByTimeAsync(POLL_TIMEOUT_MS + 4000); });
  expect(result.current.notes.map((n) => n.id)).toEqual(["n-57fb8c050382"]);
  expect(vi.mocked(api.relay).mock.calls[0][0]?.aborted).toBe(true);
});

it("polls at once when the window comes back into view", async () => {
  vi.mocked(api.relay).mockResolvedValueOnce({ notes: [], rules: [], open: 0 }).mockResolvedValue(withNote);
  const { result } = renderHook(() => useRelay());
  await act(async () => { await vi.advanceTimersByTimeAsync(0); });
  expect(result.current.notes).toEqual([]);
  await act(async () => {
    window.dispatchEvent(new Event("focus"));
    await vi.advanceTimersByTimeAsync(0);
  });
  expect(result.current.notes.map((n) => n.id)).toEqual(["n-57fb8c050382"]);
});

it("coalesces a burst of focus and visibility changes into at most one extra poll", async () => {
  // main-qa, PR #198: 20 focus/visibility cycles made 41 requests.
  vi.mocked(api.relay).mockResolvedValue(withNote);
  renderHook(() => useRelay());
  await act(async () => { await vi.advanceTimersByTimeAsync(0); });
  const before = vi.mocked(api.relay).mock.calls.length;
  await act(async () => {
    for (let i = 0; i < 20; i++) {
      window.dispatchEvent(new Event("focus"));
      document.dispatchEvent(new Event("visibilitychange"));
    }
    await vi.advanceTimersByTimeAsync(0);
  });
  expect(vi.mocked(api.relay).mock.calls.length - before).toBe(1);
});
