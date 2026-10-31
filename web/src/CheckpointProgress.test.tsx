import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { api, type CheckpointRecord } from "./api";
import { CheckpointProgress } from "./CheckpointProgress";
import { requestCheckpoint } from "./checkpointRequests";
import { sessionRef } from "./hostTransport";
vi.mock("./api", () => ({ api: { checkpointAttempt: vi.fn(), checkpoint: vi.fn() } }));
afterEach(() => { cleanup(); vi.resetAllMocks(); vi.useRealTimers(); });
const result = (state: "updated" | "reused" | "partial" | "failed"): CheckpointRecord => ({
  id: "cp", label: "manual", created_at: 1, saved: true, summary: "Previously saved summary",
  summary_update: { state, reason: state === "failed" ? "provider_timeout" : null },
  record: { prompts: [], files: [], tools: [], event_count: 0 },
});
it("does not promise rollback when a late source check fails after publication", async () => {
  const cp = result("failed"); cp.summary_update!.reason = "source_changed";
  vi.mocked(api.checkpoint).mockResolvedValue(cp);
  render(<CheckpointProgress sessionKey="late-source-progress" />);
  await act(async () => { await requestCheckpoint("late-source-progress"); });
  expect(screen.getByText(/Saved memory may have advanced/)).toBeVisible();
  expect(screen.queryByText(/summary is unchanged/)).toBeNull();
});
it("retains pending work across unmount, isolates equal keys on different hosts, and suppresses duplicate requests", async () => {
  vi.useFakeTimers();
  const local = "same-progress", remote = sessionRef("host-2", local);
  let finish!: (cp: CheckpointRecord) => void;
  vi.mocked(api.checkpoint).mockImplementationOnce(() => new Promise(resolve => { finish = resolve; }));
  let work!: Promise<CheckpointRecord>;
  const view = render(<CheckpointProgress sessionKey={local} />);
  expect(screen.queryByRole("region")).toBeNull();
  await act(async () => { work = requestCheckpoint(local); expect(requestCheckpoint(local)).toBe(work); });
  expect(screen.getByRole("progressbar")).not.toHaveAttribute("aria-valuenow");
  act(() => vi.advanceTimersByTime(5000));
  expect(screen.queryByLabelText("Elapsed time")).toBeNull();
  view.rerender(<CheckpointProgress sessionKey={remote} />);
  expect(screen.queryByRole("region")).toBeNull();
  view.unmount();
  render(<CheckpointProgress sessionKey={local} />);
  expect(screen.queryByLabelText("Elapsed time")).toBeNull();
  await act(async () => { finish(result("partial")); await work; });
  expect(screen.queryByRole("progressbar")).toBeNull();
  expect(screen.getByRole("status")).toHaveTextContent("Checkpoint failed · summary incomplete");
  expect(screen.getByText(/Some history remains unsummarized/)).toBeVisible();
  expect(api.checkpoint).toHaveBeenCalledExactlyOnceWith(local, "manual", expect.any(String));
});
it.each(["updated", "reused", "partial", "failed"] as const)("renders the returned %s outcome without a completion percentage", async state => {
  vi.mocked(api.checkpoint).mockResolvedValue(result(state));
  render(<CheckpointProgress sessionKey={state} />);
  await act(async () => { await requestCheckpoint(state); });
  const expected = { updated: "Summary updated", reused: "Summary already current", partial: "Checkpoint failed · summary incomplete", failed: "Summary update failed" };
  expect(screen.getByRole("status")).toHaveTextContent(expected[state]);
  expect(screen.queryByRole("progressbar")).toBeNull();
  expect(screen.queryByText(/100%|All history summarized/)).toBeNull();
  if (state === "failed" || state === "partial") expect(screen.getByRole("region")).toHaveClass("rd-checkpoint-progress-failure");
  if (state === "failed") expect(screen.getByText(/Any previously saved summary is unchanged/)).toBeVisible();
});
it("keeps a remote failure uncertain and does not fall back locally or alter the saved summary", async () => {
  const key = sessionRef("remote", "transport-progress");
  vi.mocked(api.checkpoint).mockRejectedValue(new Error("connection lost"));
  render(<><CheckpointProgress sessionKey={key} /><p>Last good saved summary</p></>);
  await act(async () => { await requestCheckpoint(key).catch(() => undefined); });
  expect(screen.getByRole("status")).toHaveTextContent("Result not confirmed");
  expect(screen.getByText("Last good saved summary")).toBeVisible();
  expect(screen.queryByText(/not saved|Summary update failed/)).toBeNull();
  expect(api.checkpoint).toHaveBeenCalledExactlyOnceWith(key, "manual", expect.any(String));
});

it("shows measured remote progress during POST, preserves it across navigation, and stops polling on completion", async () => {
  vi.useFakeTimers();
  const key = sessionRef("progress-host", "measured");
  let finish!: (cp: CheckpointRecord) => void;
  vi.mocked(api.checkpoint).mockImplementation(() => new Promise(resolve => { finish = resolve; }));
  vi.mocked(api.checkpointAttempt).mockImplementation(async (_key, id) => ({ request_id: id, state: "running", progress: { stage: "reviewing", completed: 20, total: 50 } }));
  const view = render(<CheckpointProgress sessionKey={key} />);
  let work!: Promise<CheckpointRecord>;
  await act(async () => { work = requestCheckpoint(key); });
  await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
  expect(screen.getByRole("progressbar")).toHaveAttribute("aria-valuenow", "20");
  expect(screen.getByRole("progressbar")).toHaveAttribute("aria-valuemax", "50");
  expect(screen.getByRole("status")).toHaveTextContent("Reviewing summary");
  expect(screen.getByText(/20 of 50 conversation entries checked/)).toBeVisible();
  expect(screen.queryByLabelText("Elapsed time")).toBeNull();
  expect(screen.getByRole("progressbar").firstChild).toHaveStyle({ width: "40%" });
  view.unmount(); render(<CheckpointProgress sessionKey={key} />);
  expect(screen.getByRole("progressbar")).toHaveAttribute("aria-valuenow", "20");
  expect(requestCheckpoint(key)).toBe(work);
  await act(async () => { finish(result("updated")); await work; });
  const count = vi.mocked(api.checkpointAttempt).mock.calls.length;
  await act(async () => { await vi.advanceTimersByTimeAsync(30000); });
  expect(api.checkpointAttempt).toHaveBeenCalledTimes(count);
  expect(api.checkpoint).toHaveBeenCalledOnce();
  expect(screen.queryByRole("progressbar")).toBeNull();
});

it.each(["wrong-id", "invalid-count"])("does not display fabricated progress from %s", async kind => {
  vi.useFakeTimers();
  let finish!: (cp: CheckpointRecord) => void;
  vi.mocked(api.checkpoint).mockImplementation(() => new Promise(resolve => { finish = resolve; }));
  vi.mocked(api.checkpointAttempt).mockImplementation(async (_key, id) => ({ request_id: kind === "wrong-id" ? "other" : id, state: "running", progress: { stage: "writing", completed: 60, total: 50 } }));
  render(<CheckpointProgress sessionKey={kind} />);
  let work!: Promise<CheckpointRecord>;
  await act(async () => { work = requestCheckpoint(kind); });
  await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
  expect(screen.getByRole("progressbar")).not.toHaveAttribute("aria-valuenow");
  await act(async () => { finish(result("failed")); await work; });
});

it("keeps compact progress measured and its final outcome honest", async () => {
  vi.useFakeTimers();
  let finish!: (cp: CheckpointRecord) => void;
  vi.mocked(api.checkpoint).mockImplementation(() => new Promise(resolve => { finish = resolve; }));
  vi.mocked(api.checkpointAttempt).mockImplementation(async (_key, id) => ({ request_id: id, state: "running", progress: { stage: "reviewing", completed: 8, total: 20 } }));
  const details = vi.fn();
  render(<CheckpointProgress sessionKey="compact-panel-progress" compact onDetails={details} />);
  let work!: Promise<CheckpointRecord>;
  await act(async () => { work = requestCheckpoint("compact-panel-progress"); });
  await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
  expect(screen.getByRole("status")).toHaveTextContent("Reviewing summary… · 8 of 20 records");
  expect(screen.getByRole("progressbar")).toHaveAttribute("aria-valuenow", "8");
  expect(screen.getByRole("progressbar")).toHaveAttribute("aria-valuemax", "20");
  await act(async () => { finish(result("partial")); await work; });
  expect(screen.getByRole("status")).toHaveTextContent("Checkpoint failed · summary incomplete");
  expect(screen.queryByRole("progressbar")).toBeNull();
  screen.getByRole("button", { name: "Details ↗" }).click();
  expect(details).toHaveBeenCalledWith("cp");
});
