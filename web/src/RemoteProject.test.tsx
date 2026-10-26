import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { RemoteProject } from "./RemoteProject";
import { destinationRequest } from "./desktop";
vi.mock("./desktop", () => ({ destinationRequest: vi.fn(), desktop: () => ({ targets: [{ id: "dev", name: "Remote — dev" }] }), selectLaunchTarget: vi.fn() }));
afterEach(() => { cleanup(); localStorage.clear(); vi.resetAllMocks(); });
const snapshot = { fingerprint: "snapshot", source: "/local", bytes: 12, entries: [{ path: "file" }], excluded: [".env"], ignored: [], git: { kind: "git" }, conversation: {} };
it("requires a review and consent before copying, then reuses the operation on retry", async () => {
  const prepared = vi.fn();
  const request = vi.mocked(destinationRequest);
  request.mockImplementation(async (_target, operation) => {
    if (operation === "project-preview") return snapshot;
    if (operation === "project-preflight") return {};
    if (operation === "project-transfer") throw new Error("Connection lost; retry");
    return {};
  });
  render(<RemoteProject target="dev" kind="copy" command="codex" onPrepared={prepared} onBusy={() => undefined} />);
  fireEvent.change(screen.getByLabelText("Local project path"), { target: { value: "/local" } });
  fireEvent.change(screen.getByLabelText("Remote destination folder"), { target: { value: "/remote/new" } });
  fireEvent.click(screen.getByText("Review transfer"));
  const copy = await screen.findByRole("button", { name: "Copy project" });
  expect(copy).toBeDisabled();
  expect(screen.getByText(".env")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("checkbox"));
  fireEvent.click(copy);
  await screen.findByRole("alert");
  const first = request.mock.calls.find(c => c[1] === "project-transfer")!;
  fireEvent.click(copy);
  await waitFor(() => expect(request.mock.calls.filter(c => c[1] === "project-transfer")).toHaveLength(2));
  expect(request.mock.calls.filter(c => c[1] === "project-transfer")[1][2]).toEqual(first[2]);
  expect(prepared).not.toHaveBeenCalled();
});
it("does not apply a stale review after changing the destination component", async () => {
  let resolve!: (v: unknown) => void;
  vi.mocked(destinationRequest).mockImplementation(async (_target, op) => op === "project-preview" ? await new Promise(r => { resolve = r; }) : {});
  const { unmount } = render(<RemoteProject target="dev" kind="copy" command="codex" onPrepared={vi.fn()} onBusy={vi.fn()} />);
  fireEvent.change(screen.getByLabelText("Local project path"), { target: { value: "/local" } });
  fireEvent.change(screen.getByLabelText("Remote destination folder"), { target: { value: "/remote/new" } });
  fireEvent.click(screen.getByText("Review transfer"));
  unmount();
  await act(async () => resolve(snapshot));
  expect(screen.queryByText("Copy project")).toBeNull();
});

it.each(["failed", "ready"])("recovers clone %s status when its original reply is lost", async stage => {
  const prepared = vi.fn();
  let late!: (value: unknown) => void;
  let attempt = "", polls = 0;
  vi.mocked(destinationRequest).mockImplementation(async (_target, op, params) => {
    if (op === "project-clone") { attempt = (params as { attempt_id: string }).attempt_id; return await new Promise(resolve => { late = resolve; }); }
    if (op === "project-status") return { id: (params as { id: string }).id, attempt_id: stage === "failed" && polls++ === 0 ? "previous-attempt" : attempt, stage, destination: "/synthetic", error: "Synthetic download failure" };
    return {};
  });
  render(<RemoteProject target="local" kind="clone" command="codex" onPrepared={prepared} onBusy={vi.fn()} />);
  fireEvent.change(screen.getByLabelText("Repository URL"), { target: { value: "https://example.invalid/test.git" } });
  fireEvent.change(screen.getByLabelText("Local destination folder"), { target: { value: "/synthetic" } });
  fireEvent.click(screen.getByText("Review transfer"));
  const clone = await screen.findByRole("button", { name: "Clone repository" });
  fireEvent.click(screen.getByRole("checkbox"));
  vi.useFakeTimers();
  try {
    fireEvent.click(clone);
    await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
    if (stage === "failed") {
      expect(clone).toBeDisabled(); // A prior retry receipt cannot finish this attempt.
      await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
    }
    expect(clone).toBeEnabled();
    if (stage === "failed") {
      expect(screen.getByRole("alert")).toHaveTextContent("Synthetic download failure");
      expect(screen.queryByRole("status")).toBeNull();
      expect(prepared).not.toHaveBeenCalled();
    } else expect(prepared).toHaveBeenCalledTimes(1);
    await act(async () => { late({ stage: "ready" }); });
    expect(prepared).toHaveBeenCalledTimes(stage === "ready" ? 1 : 0);
  } finally { vi.useRealTimers(); }
});
