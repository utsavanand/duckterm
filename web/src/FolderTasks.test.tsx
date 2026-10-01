import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { FolderTasks, FolderTask } from "./FolderTasks";
const task = (id: string, status: FolderTask["status"], at: number): FolderTask => ({ id, status, created_at: at, updated_at: at, folder: "Product", title: id, owner_session: "agent", owner_name: "Design", owner_deleted: false, activity: "Checking keyboard focus", note: "", });
afterEach(() => { vi.unstubAllGlobals(); vi.useRealTimers(); });
it("orders older active work first, filters done and opens the owning session", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, json: async () => ({ tasks: [task("Done", "done", 1), task("New", "in_progress", 3), task("Old", "in_progress", 2)] }) }));
  const open = vi.fn(); const view = render(<FolderTasks folder="Product" onOpen={open} />);
  await screen.findByText("Old");
  expect([...view.container.querySelectorAll("h4")].map(n => n.textContent)).toEqual(["Old", "New"]);
  expect(screen.queryByRole("heading", { name: "Done" })).toBeNull();
  fireEvent.click(screen.getAllByRole("button", { name: "Design ↗" })[0]); expect(open).toHaveBeenCalledWith("agent");
  fireEvent.change(screen.getByLabelText("Filter tasks"), { target: { value: "done" } });
  expect(screen.getByRole("heading", { name: "Done" })).toBeVisible();
});
it("refreshes live activity and retains a visibly stale result after a failure", async () => {
  let rows = [task("Navigation", "parked", 1)];
  const fetcher = vi.fn().mockImplementation(async () => ({ ok: true, json: async () => ({ tasks: rows }) })); vi.stubGlobal("fetch", fetcher);
  render(<FolderTasks onOpen={() => {}} />);
  await screen.findByText("Checking keyboard focus");
  rows = [{ ...rows[0], activity: "Reviewing contrast" }];
  act(() => window.dispatchEvent(new Event("folder-tasks-refresh")));
  await screen.findByText("Reviewing contrast");
  fetcher.mockRejectedValue(new Error("Connection lost"));
  act(() => window.dispatchEvent(new Event("folder-tasks-refresh")));
  await screen.findByText(/Showing last loaded tasks/);
  expect(screen.getByText("Reviewing contrast")).toBeVisible();
});
it("stops task reads after the widget is removed", async () => {
  const fetcher = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ tasks: [] }) }); vi.stubGlobal("fetch", fetcher);
  const view = render(<FolderTasks onOpen={() => {}} />);
  await waitFor(() => expect(screen.getByText(/No tasks in this view/)).toBeVisible());
  view.unmount(); vi.useFakeTimers();
  act(() => { vi.advanceTimersByTime(30000); window.dispatchEvent(new Event("folder-tasks-refresh")); });
  expect(fetcher).toHaveBeenCalledTimes(1);
});
