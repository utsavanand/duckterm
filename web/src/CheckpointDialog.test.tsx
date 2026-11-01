import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { api, type CheckpointPlan, type CheckpointRecord } from "./api";
import { CheckpointDialogHost } from "./CheckpointDialog";
import { chooseCheckpoint } from "./checkpointSelection";
import { CheckpointProgress } from "./CheckpointProgress";
import { checkpointRequestError } from "./checkpointRequests";
import { sessionRef } from "./hostTransport";

vi.mock("./api", async importOriginal => ({ ...await importOriginal<typeof import("./api")>(), api: { checkpointPlan: vi.fn(), checkpoint: vi.fn(), checkpointAttempt: vi.fn() } }));
afterEach(async () => { cleanup(); await act(async () => {}); vi.resetAllMocks(); });
function plan(pending = 47, count: number | "all" = 20): CheckpointPlan {
  const selected = count === "all" ? pending : Math.min(pending, count);
  return { available_records: pending + 8, processed_records: 8, pending_records: pending, default_records: count, selected_records: selected, remaining_records: pending - selected, order: "oldest_unprocessed_first", selection_plan: { version: 1, boundary: { source: "frozen" } } };
}
function result(id = "cp"): CheckpointRecord { return { id, label: "manual", created_at: 1, saved: true, summary: "Summary", summary_update: { state: "updated" }, record: { prompts: [], files: [], tools: [], event_count: 0 } }; }
async function open(key = "dialog-session") {
  let work!: Promise<CheckpointRecord | null>;
  await act(async () => { work = chooseCheckpoint(key, "QA"); });
  return { work };
}
it("shows pending/default/remaining counts and Cancel never creates a checkpoint", async () => {
  vi.mocked(api.checkpointPlan).mockResolvedValue(plan()); render(<CheckpointDialogHost />);
  const { work } = await open();
  expect(screen.getByText(/conversation records haven’t been summarized/)).toHaveTextContent("47");
  expect(screen.getByText(/records selected/)).toHaveTextContent("20 records selected · 27 left for later");
  expect(api.checkpoint).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
  expect(await work).toBeNull(); expect(screen.queryByRole("dialog")).toBeNull();
  expect(api.checkpoint).not.toHaveBeenCalled();
});
it.each(["oldest_unprocessed_first", "newest_unprocessed_first"] as const)("preserves the confirmed %s plan exactly", async order => {
  const preview: CheckpointPlan = { ...plan(), order, selection_plan: { version: order === "newest_unprocessed_first" ? 2 : 1, order, boundary: { source: "frozen" } } };
  vi.mocked(api.checkpointPlan).mockResolvedValue(preview); vi.mocked(api.checkpoint).mockResolvedValue(result());
  render(<CheckpointDialogHost />);
  const { work } = await open(`order-${order}`);
  expect(screen.queryByRole("alert")).toBeNull();
  await act(async () => { fireEvent.click(screen.getByRole("button", { name: "Checkpoint 20 records" })); await work; });
  expect(api.checkpoint).toHaveBeenCalledExactlyOnceWith(`order-${order}`, "manual", expect.any(String), { plan: preview.selection_plan, records: 20 });
});
it("rejects an unknown selection order before submitting", async () => {
  vi.mocked(api.checkpointPlan).mockResolvedValue({ ...plan(), order: "random" as CheckpointPlan["order"] });
  render(<CheckpointDialogHost />);
  const { work } = await open("unknown-order");
  expect(screen.getByRole("alert")).toHaveTextContent("Invalid checkpoint count response");
  fireEvent.click(screen.getByRole("button", { name: "Cancel" })); await work;
  expect(api.checkpoint).not.toHaveBeenCalled();
});
it.each(["default", "custom", "all"])("sends the %s selection once with its frozen plan and original remote session", async choice => {
  const preview = plan(); const key = sessionRef("cloud", `choice-${choice}`);
  vi.mocked(api.checkpointPlan).mockResolvedValue(preview); vi.mocked(api.checkpoint).mockResolvedValue(result());
  render(<CheckpointDialogHost />); const { work } = await open(key);
  expect(chooseCheckpoint(key, "Other label")).toBe(work);
  if (choice === "custom") fireEvent.change(screen.getByRole("spinbutton"), { target: { value: "35" } });
  if (choice === "all") fireEvent.click(screen.getByRole("radio", { name: "All 47 unprocessed records" }));
  const count = choice === "custom" ? 35 : choice === "all" ? 47 : 20;
  const submit = screen.getByRole("button", { name: `Checkpoint ${count} records` });
  await act(async () => { fireEvent.click(submit); fireEvent.click(submit); await work; });
  expect(api.checkpoint).toHaveBeenCalledExactlyOnceWith(key, "manual", expect.any(String), { plan: preview.selection_plan, records: choice === "all" ? "all" : count });
  expect(screen.queryByRole("dialog")).toBeNull();
});
it("keeps configured defaults, bounds custom counts and handles zero remaining", async () => {
  vi.mocked(api.checkpointPlan).mockResolvedValue(plan(7, 40)); render(<CheckpointDialogHost />);
  const { work } = await open("small");
  expect(screen.getByRole("radio", { name: "Earliest 7 · your configured default" })).toBeChecked();
  for (const value of ["", "0", "-2", "1.5", "1001"]) {
    fireEvent.change(screen.getByRole("spinbutton"), { target: { value } });
    expect(screen.getByRole("button", { name: "Checkpoint" })).toBeDisabled();
  }
  fireEvent.keyDown(screen.getByRole("dialog"), { key: "Escape" }); await work;
  vi.mocked(api.checkpointPlan).mockResolvedValue(plan(0)); const empty = await open("empty");
  expect(screen.getByText("Nothing new to summarize.")).toBeVisible();
  expect(screen.getByRole("button", { name: "Checkpoint" })).toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: "Cancel" })); await empty.work;
  expect(api.checkpoint).not.toHaveBeenCalled();
});
it("refuses unavailable or invalid counts and refreshes without starting work", async () => {
  vi.mocked(api.checkpointPlan).mockRejectedValueOnce(new Error("Unsupported session operation"))
    .mockResolvedValueOnce({ ...plan(), pending_records: -1 }).mockResolvedValue(plan());
  render(<CheckpointDialogHost />); const { work } = await open("retry-count");
  expect(screen.getByRole("alert")).toHaveTextContent("Nothing has started");
  await act(async () => { fireEvent.click(screen.getByRole("button", { name: "Refresh count" })); });
  expect(screen.getByRole("alert")).toHaveTextContent("Invalid checkpoint count response");
  await act(async () => { fireEvent.click(screen.getByRole("button", { name: "Refresh count" })); });
  expect(screen.getByRole("button", { name: "Checkpoint 20 records" })).toBeEnabled();
  fireEvent.click(screen.getByRole("button", { name: "Cancel" })); await work;
  expect(api.checkpoint).not.toHaveBeenCalled();
});
it("ignores a late preview after cancellation or a different session opens", async () => {
  let finish!: (value: CheckpointPlan) => void;
  vi.mocked(api.checkpointPlan).mockImplementationOnce(() => new Promise(resolve => { finish = resolve; })).mockResolvedValue(plan(3));
  render(<CheckpointDialogHost />); const first = await open("old");
  const second = await open("new"); expect(await first.work).toBeNull();
  await act(async () => { finish(plan(900)); });
  expect(screen.getByRole("button", { name: "Checkpoint 3 records" })).toBeEnabled();
  fireEvent.click(screen.getByRole("button", { name: "Cancel" })); await second.work;
  expect(api.checkpoint).not.toHaveBeenCalled();
});
it("uses an exact failed receipt to distinguish changed selection from an uncertain connection", async () => {
  vi.mocked(api.checkpointPlan).mockResolvedValue(plan());
  vi.mocked(api.checkpoint).mockRejectedValue(new Error("checkpoint_source_changed"));
  vi.mocked(api.checkpointAttempt).mockImplementation(async (_key, request_id) => ({ request_id, state: "failed", error: "checkpoint_source_changed" }));
  render(<><CheckpointDialogHost /><CheckpointProgress sessionKey="stale-selection" /></>);
  const { work } = await open("stale-selection");
  const caught = work.catch(error => error);
  await act(async () => { fireEvent.click(screen.getByRole("button", { name: "Checkpoint 20 records" })); await caught; });
  expect(checkpointRequestError(await caught)).toContain("refresh the count");
  expect(screen.getByRole("status")).toHaveTextContent("Checkpoint not saved");
  expect(screen.queryByText("Result not confirmed")).toBeNull();
  expect(api.checkpoint).toHaveBeenCalledOnce();
});
it("preselects all unprocessed records when the configured default is all", async () => {
  const preview = plan(2014, "all");
  vi.mocked(api.checkpointPlan).mockResolvedValue(preview); vi.mocked(api.checkpoint).mockResolvedValue(result());
  render(<CheckpointDialogHost />); const { work } = await open("default-all");
  expect(screen.getByRole("radio", { name: "All 2,014 unprocessed records" })).toBeChecked();
  expect(screen.queryByRole("radio", { name: /your configured default/ })).toBeNull();
  expect(screen.getByText(/records selected/)).toHaveTextContent("2,014 records selected · 0 left for later");
  // More than 1,000 pending records must still submit; the custom count's max cannot block "All".
  await act(async () => { fireEvent.click(screen.getByRole("button", { name: "Checkpoint 2,014 records" })); });
  expect(api.checkpoint).toHaveBeenCalledExactlyOnceWith("default-all", "manual", expect.any(String), { plan: preview.selection_plan, records: "all" });
  await act(async () => { await work; });
});
it("accepts a plan from an older host that still reports batch_records", async () => {
  vi.mocked(api.checkpointPlan).mockResolvedValue({ ...plan(), batch_records: 20 } as CheckpointPlan);
  render(<CheckpointDialogHost />); const { work } = await open("older-host");
  expect(screen.queryByRole("alert")).toBeNull();
  expect(screen.getByRole("button", { name: "Checkpoint 20 records" })).toBeEnabled();
  fireEvent.click(screen.getByRole("button", { name: "Cancel" })); await work;
});
