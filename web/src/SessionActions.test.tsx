import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { api } from "./api";
import { SessionActions } from "./SessionActions";
import type { SessionView } from "./types";
import { beginRecoveryUndo, finishRecoveryUndo, setRecoveryResumeAllowed, useRecoveryResumeBlocked } from "./resumeReadiness";
import { destinationRequest } from "./desktop";
const { toast } = vi.hoisted(() => ({ toast: vi.fn() }));
vi.mock("./api", () => ({ api: { checkpoint: vi.fn() } }));
vi.mock("./ui", () => ({ useToast: () => toast }));
vi.mock("./useResumeSession", () => ({ useResumeSession: (key: string) => ({ resuming: false, recoveryBlocked: useRecoveryResumeBlocked(key), resumeSession: vi.fn() }) }));
vi.mock("./desktop", () => ({ desktop: () => ({ currentTarget: "local" }), destinationRequest: vi.fn() }));
vi.mock("./RestartControls", () => ({ RestartControls: () => null }));
vi.mock("./ForkMergeDialog", () => ({ ForkMergeDialog: () => null }));
afterEach(() => { cleanup(); vi.resetAllMocks(); vi.restoreAllMocks(); });
it("checkpoint reports failure without closing the menu, then reports incomplete export honestly", async () => {
  const close = vi.fn();
  const s = { key: "remote:build:one", label: "QA", state: "busy", startedAt: 1, runtime: "generic" } as SessionView;
  render(<SessionActions session={s} anchor={{ key: s.key, x: 10, y: 10, trigger: document.body }} onClose={close} onFork={vi.fn()} onRename={vi.fn()} onDelete={vi.fn()} onNotes={vi.fn()} />);
  vi.mocked(api.checkpoint).mockRejectedValueOnce(new Error("offline"));
  await act(async () => { fireEvent.click(screen.getByRole("menuitem", { name: "Checkpoint" })); });
  expect(api.checkpoint).toHaveBeenCalledWith(s.key, "manual");
  expect(toast).toHaveBeenLastCalledWith("Checkpoint failed: offline", "err");
  expect(close).not.toHaveBeenCalled();
  vi.mocked(api.checkpoint).mockResolvedValueOnce({ id: "cp", label: "manual", created_at: 1, summary: "", saved: true, summary_state: "unavailable", export_reason: "markdown_unavailable", record: { prompts: [], files: [], tools: [], event_count: 0 } });
  await act(async () => { fireEvent.click(screen.getByRole("menuitem", { name: "Checkpoint" })); });
  expect(toast).toHaveBeenLastCalledWith("Summary unavailable · Markdown export unavailable", "ok");
  expect(close).toHaveBeenCalledOnce();
});

it("blocks local continuation while conversation Undo is unresolved, including after reopening the menu", async () => {
  const s = { key: "continue-undo", label: "Recovery", state: "stopped", startedAt: 1, runtime: "codex", launched: true } as SessionView;
  const open = () => render(<SessionActions session={s} anchor={{ key: s.key, x: 10, y: 10, trigger: document.body }} onClose={vi.fn()} onFork={vi.fn()} onRename={vi.fn()} onDelete={vi.fn()} onNotes={vi.fn()} />);
  const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
  const first = open();
  try {
    expect(screen.getByRole("menuitem", { name: "Continue locally" })).toBeEnabled();
    act(() => { beginRecoveryUndo(s.key); });
    expect(screen.getByRole("menuitem", { name: "Continue locally" })).toBeDisabled();
    first.unmount(); open();
    await act(async () => { fireEvent.click(screen.getByRole("menuitem", { name: "Continue locally" })); });
    expect(confirm).not.toHaveBeenCalled();
    expect(destinationRequest).not.toHaveBeenCalled();
    act(() => { finishRecoveryUndo(s.key); });
    expect(screen.getByRole("menuitem", { name: "Continue locally" })).toBeDisabled();
    act(() => { setRecoveryResumeAllowed(s.key, true); });
    await act(async () => { fireEvent.click(screen.getByRole("menuitem", { name: "Continue locally" })); });
    expect(destinationRequest).toHaveBeenCalledExactlyOnceWith("local", "project-continue", { source_session: s.key });
  } finally { cleanup(); finishRecoveryUndo(s.key); setRecoveryResumeAllowed(s.key, true); }
});

it("shows a failed summary outcome as an error even when its attempt record saved", async () => {
  const s = { key: "one", label: "QA", state: "busy", startedAt: 1, runtime: "generic" } as SessionView;
  const close = vi.fn();
  render(<SessionActions session={s} anchor={{ key: s.key, x: 10, y: 10, trigger: document.body }} onClose={close} onFork={vi.fn()} onRename={vi.fn()} onDelete={vi.fn()} onNotes={vi.fn()} />);
  vi.mocked(api.checkpoint).mockResolvedValueOnce({ id: "cp", label: "manual", created_at: 1, summary: "", saved: true, summary_state: "unavailable", summary_update: { state: "failed", reason: "provider_timeout" }, record: { prompts: [], files: [], tools: [], event_count: 0 } });
  await act(async () => { fireEvent.click(screen.getByRole("menuitem", { name: "Checkpoint" })); });
  expect(toast).toHaveBeenLastCalledWith("Summary update failed · The summary provider timed out", "err");
  expect(close).toHaveBeenCalledOnce();
});
