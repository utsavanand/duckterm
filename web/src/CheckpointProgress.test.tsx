import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { api, type CheckpointRecord } from "./api";
import { CheckpointProgress } from "./CheckpointProgress";
import { requestCheckpoint } from "./checkpointRequests";
import { sessionRef } from "./hostTransport";
vi.mock("./api", () => ({ api: { checkpoint: vi.fn() } }));
afterEach(() => { cleanup(); vi.resetAllMocks(); vi.useRealTimers(); });
const result = (state: "updated" | "reused" | "partial" | "failed"): CheckpointRecord => ({
  id: "cp", label: "manual", created_at: 1, saved: true, summary: "Previously saved summary",
  summary_update: { state, reason: state === "failed" ? "provider_timeout" : null },
  record: { prompts: [], files: [], tools: [], event_count: 0 },
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
  expect(screen.getByLabelText("Elapsed time")).toHaveTextContent("5s");
  view.rerender(<CheckpointProgress sessionKey={remote} />);
  expect(screen.queryByRole("region")).toBeNull();
  view.unmount();
  render(<CheckpointProgress sessionKey={local} />);
  expect(screen.getByLabelText("Elapsed time")).toHaveTextContent("5s");
  await act(async () => { finish(result("partial")); await work; });
  expect(screen.queryByRole("progressbar")).toBeNull();
  expect(screen.getByRole("status")).toHaveTextContent("Partial summary");
  expect(screen.getByText(/Some history remains unsummarized/)).toBeVisible();
  expect(api.checkpoint).toHaveBeenCalledExactlyOnceWith(local, "manual");
});
it.each(["updated", "reused", "partial", "failed"] as const)("renders the returned %s outcome without a completion percentage", async state => {
  vi.mocked(api.checkpoint).mockResolvedValue(result(state));
  render(<CheckpointProgress sessionKey={state} />);
  await act(async () => { await requestCheckpoint(state); });
  const expected = { updated: "Summary updated", reused: "Summary already current", partial: "Partial summary", failed: "Summary update failed" };
  expect(screen.getByRole("status")).toHaveTextContent(expected[state]);
  expect(screen.queryByRole("progressbar")).toBeNull();
  expect(screen.queryByText(/100%|All history summarized/)).toBeNull();
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
  expect(api.checkpoint).toHaveBeenCalledExactlyOnceWith(key, "manual");
});
