import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { api } from "./api";
import { RestartControls } from "./RestartControls";
import { SessionView } from "./types";
import { sessionRef } from "./hostTransport";

vi.mock("./api", () => ({ api: { restartStatus: vi.fn(), restart: vi.fn(), cancelRestart: vi.fn() } }));
const session: SessionView = { key: "a", label: "My project", state: "busy", runtime: "codex", model: "current-model", lastEventType: "PreToolUse", startedAt: 1, updatedAt: 1, eventCount: 1 };
const ready = { can_restart: true, draft_clear: true, after_turn: true, model: "current-model" };
beforeEach(() => { vi.mocked(api.restartStatus).mockResolvedValue(ready); });
afterEach(() => { cleanup(); vi.resetAllMocks(); vi.useRealTimers(); });

it("queues a model change with the current model preset and a cancelable status", async () => {
  vi.mocked(api.restart).mockResolvedValue({ ...ready, status: "queued", requested_model: "new-model" });
  vi.mocked(api.cancelRestart).mockResolvedValue({ ...ready, status: "canceled" });
  render(<RestartControls session={session} />);
  const open = screen.getByRole("button", { name: "Change model" });
  await waitFor(() => expect(open).toBeEnabled());
  fireEvent.click(open);
  expect(screen.getByLabelText("Model after restart")).toHaveValue("current-model");
  fireEvent.change(screen.getByLabelText("Model after restart"), { target: { value: "new-model" } });
  fireEvent.click(screen.getByRole("button", { name: "Restart after this turn" }));
  await waitFor(() => expect(api.restart).toHaveBeenCalledWith("a", "new-model"));
  expect(await screen.findByText(/Restart pending/)).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "Cancel restart" }));
  await waitFor(() => expect(api.cancelRestart).toHaveBeenCalledWith("a"));
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
});

it("shows why drafts block restart without sending a restart request", async () => {
  render(<RestartControls session={session} />);
  await waitFor(() => expect(screen.getByRole("button", { name: "Restart" })).toBeEnabled());
  fireEvent.click(screen.getByRole("button", { name: "Restart" }));
  fireEvent.change(screen.getByLabelText("Model after restart"), { target: { value: "my-choice" } });
  vi.mocked(api.restartStatus).mockResolvedValue({ ...ready, draft_clear: false, reason: "Unsent text — clear your draft first." });
  // Re-open fetches an execution eligibility check; the dialog never silently sends.
  fireEvent.keyDown(window, { key: "Escape" });
  fireEvent.click(screen.getByRole("button", { name: "Restart" }));
  await waitFor(() => expect(screen.getByRole("button", { name: "Restart after this turn" })).toBeDisabled());
  expect(screen.getAllByText("Unsent text — clear your draft first.").length).toBeGreaterThan(0);
  expect(api.restart).not.toHaveBeenCalled();
});

it("refuses unsupported identities and explains local-only availability without remote calls", async () => {
  vi.mocked(api.restartStatus).mockResolvedValue({ can_restart: false, reason: "Cannot verify this conversation." });
  const view = render(<RestartControls session={session} />);
  expect(await screen.findByText("Cannot verify this conversation.")).toBeVisible();
  expect(screen.getByRole("button", { name: "Restart" })).toBeDisabled();
  vi.mocked(api.restartStatus).mockClear();
  view.rerender(<RestartControls session={{ ...session, key: sessionRef("remote", "a") }} />);
  expect(screen.getByText(/available on This Mac only/)).toBeVisible();
  expect(api.restartStatus).not.toHaveBeenCalled();
});

it("renders a durable pending request after remount and the reported new CLI version", async () => {
  vi.mocked(api.restartStatus).mockResolvedValue({ ...ready, status: "queued" });
  const view = render(<RestartControls session={session} />);
  expect(await screen.findByRole("button", { name: "Cancel restart" })).toBeEnabled();
  view.unmount();
  vi.mocked(api.restartStatus).mockResolvedValue({ ...ready, status: "completed", cli_version: "codex 2.0", previous_cli_version: "codex 1.0" });
  await act(async () => { render(<RestartControls session={session} />); });
  expect(screen.getByText(/codex 1.0 → codex 2.0/)).toBeVisible();
});
