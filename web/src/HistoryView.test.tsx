import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { HistoryView } from "./HistoryView";
import { api, forkMergeHistory } from "./api";
import { routedFetch } from "./hostTransport";
import { viewFromPersisted } from "./types";
vi.mock("./hostTransport", () => ({ routedFetch: vi.fn(), splitSessionRef: (key: string) => ({ key }) }));
vi.mock("./api", () => ({ api: { checkpoints: vi.fn() }, forkMergeHistory: vi.fn(), forkMergeService: vi.fn() }));
const session = (key: string) => viewFromPersisted({ session_key: key, state: "idle", started_at: 1, updated_at: 1, event_count: 1 } as never);
const response = (text: string) => ({ ok: true, json: async () => ({ items: [{ id: text, bucket: "deliverables", text, status: "active", created_at: 1 }] }) } as Response);
function visibility(value: string) { Object.defineProperty(document, "visibilityState", { value, configurable: true }); document.dispatchEvent(new Event("visibilitychange")); }
beforeEach(() => {
  vi.useFakeTimers(); vi.setSystemTime(100_000); visibility("visible");
  vi.mocked(routedFetch).mockResolvedValue(response("Current report"));
  vi.mocked(api.checkpoints).mockResolvedValue({ checkpoints: [] });
  vi.mocked(forkMergeHistory).mockResolvedValue({ merges: [] });
});
afterEach(() => { cleanup(); vi.useRealTimers(); vi.resetAllMocks(); Reflect.deleteProperty(document, "visibilityState"); });
it("pauses hidden polls, refreshes on return, and suspends a covered History view", async () => {
  const view = render(<HistoryView session={session("a")} />); await act(async () => {});
  expect(routedFetch).toHaveBeenCalledTimes(1);
  act(() => visibility("hidden"));
  await act(async () => { await vi.advanceTimersByTimeAsync(30_000); });
  expect(routedFetch).toHaveBeenCalledTimes(1); expect(forkMergeHistory).toHaveBeenCalledTimes(1);
  await act(async () => { visibility("visible"); });
  expect(routedFetch).toHaveBeenCalledTimes(2); expect(forkMergeHistory).toHaveBeenCalledTimes(2);
  view.rerender(<HistoryView session={session("a")} active={false} />);
  await act(async () => { await vi.advanceTimersByTimeAsync(20_000); visibility("visible"); });
  expect(routedFetch).toHaveBeenCalledTimes(2); expect(screen.getByText("Current report")).toBeInTheDocument();
  view.rerender(<HistoryView session={session("a")} />); await act(async () => {});
  expect(routedFetch).toHaveBeenCalledTimes(3);
  view.unmount(); await act(async () => { await vi.advanceTimersByTimeAsync(20_000); visibility("visible"); });
  expect(routedFetch).toHaveBeenCalledTimes(3);
});
it("skips hidden mount and overlapping requests, and ignores late replies from a previous session", async () => {
  let finish!: (r: Response) => void;
  vi.mocked(routedFetch).mockImplementationOnce(() => new Promise(resolve => { finish = resolve; }));
  act(() => visibility("hidden")); const view = render(<HistoryView session={session("old")} />);
  await act(async () => { await vi.advanceTimersByTimeAsync(20_000); });
  expect(routedFetch).not.toHaveBeenCalled(); expect(api.checkpoints).not.toHaveBeenCalled();
  await act(async () => { visibility("visible"); await vi.advanceTimersByTimeAsync(30_000); });
  expect(routedFetch).toHaveBeenCalledTimes(1);
  view.rerender(<HistoryView session={session("new")} />); await act(async () => {});
  expect(screen.getByText("Current report")).toBeInTheDocument();
  await act(async () => { finish(response("Old report")); });
  expect(screen.queryByText("Old report")).not.toBeInTheDocument(); expect(screen.getByText("Current report")).toBeInTheDocument();
});
