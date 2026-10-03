import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { ForkMergeDialog, ForkMergePreview, ForkMergeRecord, ForkMergeService } from "./ForkMergeDialog";
const preview: ForkMergePreview = { child: { key: "child", name: "navigation-review", branch: "review/navigation", commit: "7a2c91e" }, parent: { key: "parent", name: "app-dev", model: "gpt-6-astra", state: "busy", priorityDelivery: true }, summary: "Summary of the fork, not the full thread.\n\nKeyboard review completed.", allowed: true };
const receipt: ForkMergeRecord = { id: "merge-one", summary: "exact", keepOpen: false, delivery: "pending next turn", checkpoint: "pending" };
function service(): ForkMergeService { return { preview: vi.fn().mockResolvedValue(preview), send: vi.fn().mockResolvedValue(receipt), status: vi.fn().mockResolvedValue(receipt) }; }
afterEach(() => { cleanup(); vi.useRealTimers(); Object.defineProperty(document, "visibilityState", { configurable: true, value: "visible" }); });
it("reviews an editable summary and defaults to closing the child without sending early", async () => {
  const api = service(), saved = vi.fn();
  render(<ForkMergeDialog service={api} onClose={vi.fn()} onSaved={saved} />);
  const editor = await screen.findByRole("textbox", { name: /Summary to send/ });
  expect(screen.getByRole("checkbox", { name: "Keep child open" })).not.toBeChecked();
  expect(api.send).not.toHaveBeenCalled();
  expect(screen.getByText(/does not merge code/)).toBeVisible();
  expect(screen.getByText(/checkpoint is created.*only when/)).toBeVisible();
  const exact = "  Résumé 🦆\nKeep every space.\n";
  fireEvent.change(editor, { target: { value: exact } });
  fireEvent.click(screen.getByRole("button", { name: "Send summary & close child" }));
  await waitFor(() => expect(saved).toHaveBeenCalledWith(receipt));
  expect(api.send).toHaveBeenCalledWith({ summary: exact, keepOpen: false, requestKey: expect.any(String) });
  expect(screen.getByText("pending next turn")).toBeVisible();
  expect(screen.getByText("Parent merge checkpoint will be created at delivery.")).toBeVisible();
});
it("keeps edits on an uncertain send and retries the same request key", async () => {
  const api = service(); vi.mocked(api.send).mockRejectedValue(new Error("offline"));
  render(<ForkMergeDialog service={api} onClose={vi.fn()} onSaved={vi.fn()} />);
  const editor = await screen.findByRole("textbox", { name: /Summary to send/ });
  fireEvent.change(editor, { target: { value: "My edit" } });
  fireEvent.click(screen.getByRole("checkbox", { name: "Keep child open" }));
  const button = screen.getByRole("button", { name: /^Send summary$/ });
  fireEvent.click(button); await screen.findByRole("alert");
  expect(editor).toHaveValue("My edit"); expect(screen.getByRole("alert")).toHaveTextContent("note may already be saved");
  const first = vi.mocked(api.send).mock.calls[0][0];
  expect(first.keepOpen).toBe(true);
  fireEvent.click(button); await waitFor(() => expect(api.send).toHaveBeenCalledTimes(2));
  expect(vi.mocked(api.send).mock.calls[1][0]).toEqual(first);
  await waitFor(() => expect(button).toBeEnabled());
  fireEvent.change(editor, { target: { value: "Another edit" } });
  fireEvent.click(button); await waitFor(() => expect(api.send).toHaveBeenCalledTimes(3));
  expect(vi.mocked(api.send).mock.calls[2][0].requestKey).not.toBe(first.requestKey);
});
it("keeps a deleted parent visible and never offers a send", async () => {
  const api = service(); vi.mocked(api.preview).mockResolvedValue({ ...preview, parent: null, allowed: false, reason: "The original parent was deleted." });
  render(<ForkMergeDialog service={api} onClose={vi.fn()} onSaved={vi.fn()} />);
  await screen.findByText("Parent deleted");
  expect(screen.getByRole("button", { name: "Send summary & close child" })).toBeDisabled();
  expect(api.send).not.toHaveBeenCalled();
});
it("reports inbox-only parents without claiming automatic delivery", async () => {
  const api = service(); vi.mocked(api.preview).mockResolvedValue({ ...preview, parent: { ...preview.parent!, priorityDelivery: false } });
  render(<ForkMergeDialog service={api} onClose={vi.fn()} onSaved={vi.fn()} />);
  await screen.findByText(/automatic delivery is unavailable/);
  expect(screen.getByText("gpt-6-astra")).toBeVisible();
});
it("reports a preview failure with a usable retry", async () => {
  const api = service(); vi.mocked(api.preview).mockRejectedValueOnce(new Error("server unavailable")).mockResolvedValue(preview);
  render(<ForkMergeDialog service={api} onClose={vi.fn()} onSaved={vi.fn()} />);
  await screen.findByRole("alert"); fireEvent.click(screen.getByRole("button", { name: "Retry" }));
  await screen.findByRole("textbox", { name: /Summary to send/ });
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
});
it("uses server delivery/checkpoint status and pauses polling while hidden", async () => {
  vi.useFakeTimers(); const api = service();
  vi.mocked(api.status).mockResolvedValue({ ...receipt, delivery: "delivered", checkpoint: "created" });
  render(<ForkMergeDialog service={api} onClose={vi.fn()} onSaved={vi.fn()} />);
  await act(async () => {});
  await act(async () => { fireEvent.click(screen.getByRole("button", { name: "Send summary & close child" })); });
  expect(screen.getByText("Parent merge checkpoint created.")).toBeVisible();
  Object.defineProperty(document, "visibilityState", { configurable: true, value: "hidden" });
  await act(async () => { document.dispatchEvent(new Event("visibilitychange")); vi.advanceTimersByTime(10000); });
  expect(api.status).toHaveBeenCalledTimes(1);
  vi.mocked(api.status).mockRejectedValue(new Error("offline"));
  Object.defineProperty(document, "visibilityState", { configurable: true, value: "visible" });
  await act(async () => { document.dispatchEvent(new Event("visibilitychange")); });
  expect(screen.getByRole("status")).toHaveTextContent("Showing the last known state");
  expect(screen.getByText("delivered")).toBeVisible();
});
