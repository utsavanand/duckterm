import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { api, forkMergeHistory, TimelinePage } from "./api";
import { TimelineView, TIMELINE_MILESTONES } from "./TimelineView";
import type { SessionView } from "./types";
vi.mock("./api", () => ({ api: { timeline: vi.fn(), checkpoints: vi.fn() }, forkMergeHistory: vi.fn(), forkMergeService: vi.fn() }));
vi.mock("./HistoryView", () => ({ HistoryView: () => <p>Earlier digest history</p> }));
const session = { key: "one", label: "Test", startedAt: 1 } as SessionView;
const page = (text = "Saved work", cursor: string | null = null): TimelinePage => ({ summary: { text: "", updated_at: null, total: 1, counts: {} }, entries: [{ id: text, ts: 1791300000000, kind: "delivered", one_line: text, detail: { text }, refs: [] }], next_cursor: cursor });
beforeEach(() => {
  vi.mocked(api.timeline).mockResolvedValue(page());
  vi.mocked(api.checkpoints).mockResolvedValue({ checkpoints: [] });
  vi.mocked(forkMergeHistory).mockResolvedValue({ merges: [] });
  Object.defineProperty(document, "visibilityState", { configurable: true, value: "visible" });
});
afterEach(() => { cleanup(); vi.useRealTimers(); vi.resetAllMocks(); });
it("resets pagination when filtering", async () => {
  vi.mocked(api.timeline).mockImplementation(async (_key, kinds, before) => before ? page("Older work") : page(kinds === TIMELINE_MILESTONES ? "First page" : kinds, kinds === TIMELINE_MILESTONES ? "cursor-one" : null));
  render(<TimelineView session={session} />);
  await screen.findByText("First page");
  fireEvent.click(await screen.findByRole("button", { name: "Load older entries" }));
  await screen.findByText("Older work");
  expect(api.timeline).toHaveBeenCalledWith("one", TIMELINE_MILESTONES, "cursor-one");
  fireEvent.click(screen.getByRole("button", { name: "Checkpoints" }));
  await screen.findByText("checkpoint");
  expect(screen.queryByText("Older work")).toBeNull();
  expect(api.timeline).toHaveBeenLastCalledWith("one", "checkpoint");
});
it("ignores late responses from the previous session", async () => {
  let finish!: (p: TimelinePage) => void;
  vi.mocked(api.timeline).mockImplementationOnce(() => new Promise(resolve => { finish = resolve; }));
  const view = render(<TimelineView session={session} />);
  view.rerender(<TimelineView session={{ ...session, key: "two" }} />);
  await screen.findByText("Saved work");
  await act(async () => { finish(page("Wrong session")); });
  expect(screen.queryByText("Wrong session")).toBeNull();
});
it("pauses polling while hidden or inactive and resumes on visibility", async () => {
  vi.useFakeTimers();
  const view = render(<TimelineView session={session} />);
  await act(async () => {});
  expect(api.timeline).toHaveBeenCalledTimes(1);
  Object.defineProperty(document, "visibilityState", { configurable: true, value: "hidden" });
  await act(async () => { vi.advanceTimersByTime(30_000); });
  expect(api.timeline).toHaveBeenCalledTimes(1);
  Object.defineProperty(document, "visibilityState", { configurable: true, value: "visible" });
  await act(async () => { document.dispatchEvent(new Event("visibilitychange")); });
  expect(api.timeline).toHaveBeenCalledTimes(2);
  view.rerender(<TimelineView session={session} active={false} />);
  await act(async () => { vi.advanceTimersByTime(30_000); });
  expect(api.timeline).toHaveBeenCalledTimes(2);
});
it("does not overlap slow loads", async () => {
  vi.useFakeTimers();
  vi.mocked(api.timeline).mockImplementation(() => new Promise(() => undefined));
  render(<TimelineView session={session} />);
  await act(async () => { vi.advanceTimersByTime(40_000); });
  expect(api.timeline).toHaveBeenCalledTimes(1);
});
it("keeps earlier servers readable and displays real failures", async () => {
  vi.mocked(api.timeline).mockRejectedValueOnce(new Error("404 Not found"));
  const view = render(<TimelineView session={session} />);
  await screen.findByText("Earlier digest history");
  vi.mocked(api.timeline).mockRejectedValue(new Error("Access denied"));
  view.rerender(<TimelineView session={{ ...session, key: "other" }} />);
  await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("Access denied"));
  expect(screen.queryByText("Earlier digest history")).toBeNull();
});
it("uses source time and keeps malformed legacy records and merge history readable", async () => {
  const p = page(); p.entries = [{ id: "cp", ts: 1791300000000, kind: "checkpoint", one_line: "Before switch", detail: { text: "Before switch" }, refs: [{ source: "checkpoints", id: "cp" }] }];
  vi.mocked(api.timeline).mockResolvedValue(p);
  vi.mocked(api.checkpoints).mockResolvedValue({ checkpoints: [{ id: "cp", label: "Before switch", summary: "Original summary", created_at: 1791300000000, saved: true, summary_state: "unavailable", summary_source_at: null, record: {} as never }] });
  vi.mocked(forkMergeHistory).mockResolvedValue({ merges: [{ id: "m", createdAt: 1, summary: "Child findings", checkpoint: "cp", keepOpen: true, delivery: "delivered" } as never] });
  render(<TimelineView session={session} />);
  await screen.findByText("Original summary");
  fireEvent.click(screen.getByText("Before switch"));
  expect(screen.getByText("Unavailable")).toBeVisible();
  expect(screen.getByText("Unknown")).toBeVisible();
  expect(screen.getByText("Not ready")).toBeVisible();
  expect(screen.getByText("Fork merges")).toBeVisible();
  expect(screen.getByText("Child findings")).toBeInTheDocument();
});

it("preserves the complete owner-reviewed brief and identifies its origin", async () => {
  const text = "Reviewed work. ".repeat(400) + "Known gaps: verify the remote host.";
  const p = page(); p.entries = [{ id: "cp", ts: 1, kind: "checkpoint", one_line: "Owner-reviewed handoff", detail: { text: "Owner-reviewed handoff" }, refs: [{ source: "checkpoints", id: "cp" }] }];
  vi.mocked(api.timeline).mockResolvedValue(p);
  vi.mocked(api.checkpoints).mockResolvedValue({ checkpoints: [{ id: "cp", label: "Owner-reviewed handoff", summary: text, created_at: 1, saved: true, summary_origin: "owner-reviewed", record: {} as never }] });
  render(<TimelineView session={session} />);
  await screen.findByText(text);
  fireEvent.click(screen.getByText("Owner-reviewed handoff"));
  expect(screen.getByText("Owner-reviewed brief")).toBeVisible();
  expect(screen.getByText(text).textContent).toBe(text);
});

it("retains remote history when an older native wrapper lacks Timeline transport", async () => {
  const { sessionRef } = await import("./hostTransport");
  vi.mocked(api.timeline).mockRejectedValue(new Error("Unsupported session operation"));
  const view = render(<TimelineView session={{ ...session, key: sessionRef("other-mac", "same") }} />);
  await screen.findByText("Earlier digest history");
  view.rerender(<TimelineView session={session} />);
  await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("Unsupported session operation"));
  expect(screen.queryByText("Earlier digest history")).toBeNull();
});

it("requests milestones without ordinary prompts and keeps general Messages navigation", async () => {
  const onMessages = vi.fn(); render(<TimelineView session={session} onMessages={onMessages} />);
  await screen.findByText("Saved work"); expect(api.timeline).toHaveBeenCalledWith("one", TIMELINE_MILESTONES);
  expect(TIMELINE_MILESTONES.split(",")).not.toContain("prompt");
  for (const kind of ["checkpoint", "artifact", "decision", "delivered", "completed", "restart", "model", "harness"]) expect(TIMELINE_MILESTONES.split(",")).toContain(kind);
  fireEvent.click(screen.getByRole("button", { name: "Progress" }));
  await waitFor(() => expect(api.timeline).toHaveBeenLastCalledWith("one", "delivered,learned,next_action,completed,decision,restart,model,harness,needs-you"));
  fireEvent.click(screen.getByRole("button", { name: "Messages" })); expect(onMessages).toHaveBeenCalledOnce();
  expect(screen.queryByText(/View supporting messages/)).not.toBeInTheDocument();
});
