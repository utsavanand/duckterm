import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { api, RestartOptions } from "./api";
import { RestartControls } from "./RestartControls";
import { SessionView } from "./types";
import { sessionRef } from "./hostTransport";

vi.mock("./api", () => ({ api: { restartStatus: vi.fn(), restart: vi.fn(), cancelRestart: vi.fn(), restartOptions: vi.fn(), models: vi.fn() } }));
const session: SessionView = { key: "a", label: "My project", state: "busy", runtime: "codex", model: "current-model", lastEventType: "PreToolUse", startedAt: 1, updatedAt: 1, eventCount: 1 };
const ready = { can_restart: true, draft_clear: true, after_turn: true, model: "current-model" };
const options: RestartOptions = { current: { harness: "codex", model: "current-model" }, resume_restart: { available: true }, draft_clear: true, after_turn: true,
  harnesses: [
    { name: "codex", available: true, context: "native", model_selection: { available: true }, models: [{ id: "new-model", label: "New model 1.0" }] },
    { name: "claude-code", available: true, context: "seeded_new_conversation", model_selection: { available: true }, models: [{ id: "claude-exact", label: "Claude exact" }] },
  ] };
beforeEach(() => { vi.mocked(api.restartOptions).mockResolvedValue(structuredClone(options)); vi.mocked(api.restartStatus).mockResolvedValue(ready); vi.mocked(api.models).mockResolvedValue({ models: [{ id: "new-model", label: "New model 1.0" }] }); });
afterEach(() => { cleanup(); vi.resetAllMocks(); vi.useRealTimers(); });

it("queues a model change with the current model preset and a cancelable status", async () => {
  vi.mocked(api.restart).mockImplementation(async () => {
    const queued = { ...ready, status: "queued" as const, requested_model: "new-model" };
    vi.mocked(api.restartStatus).mockResolvedValue(queued);
    return queued;
  });
  vi.mocked(api.cancelRestart).mockResolvedValue({ ...ready, status: "canceled" });
  render(<RestartControls session={session} />);
  const open = screen.getByRole("button", { name: "Change model" });
  await waitFor(() => expect(open).toBeEnabled());
  fireEvent.click(open);
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  fireEvent.click(await screen.findByRole("menuitemradio", { name: /New model 1.0/ }));
  await waitFor(() => expect(screen.getByLabelText("Model")).toHaveValue("new-model"));
  fireEvent.click(screen.getByRole("button", { name: "Restart after this turn" }));
  await waitFor(() => expect(api.restart).toHaveBeenCalledWith("a", "new-model", "codex"));
  expect(await screen.findByText(/Restart pending/)).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "Cancel restart" }));
  await waitFor(() => expect(api.cancelRestart).toHaveBeenCalledWith("a"));
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
});

it("shows why drafts block restart without sending a restart request", async () => {
  render(<RestartControls session={session} />);
  await waitFor(() => expect(screen.getByRole("button", { name: "Restart" })).toBeEnabled());
  fireEvent.click(screen.getByRole("button", { name: "Restart" }));
  await waitFor(() => expect(screen.getByLabelText("Model")).toBeEnabled());
  vi.mocked(api.restartStatus).mockResolvedValue({ ...ready, draft_clear: false, reason: "Unsent text — clear your draft first." });
  // Re-open fetches an execution eligibility check; the dialog never silently sends.
  fireEvent.keyDown(screen.getByRole("dialog"), { key: "Escape" });
  fireEvent.click(screen.getByRole("button", { name: "Restart" }));
  await waitFor(() => expect(screen.getByRole("button", { name: "Restart after this turn" })).toBeDisabled());
  expect(await screen.findByText("Send or clear unsent terminal text before restarting.")).toBeVisible();
  expect(api.restart).not.toHaveBeenCalled();
});

it("refuses unsupported identities and explains local-only availability without remote calls", async () => {
  vi.mocked(api.restartStatus).mockResolvedValue({ can_restart: false, reason: "Cannot verify this conversation." });
  const view = render(<RestartControls session={session} />);
  await waitFor(() => expect(screen.getByRole("button", { name: "Change model" })).toBeDisabled());
  expect(screen.getByRole("button", { name: "Restart" })).toBeEnabled();
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

it("keeps restart usable when catalog fails, retries, and never restarts from selecting current model", async () => {
  vi.mocked(api.models).mockRejectedValueOnce(new Error("Sign in and retry"));
  render(<RestartControls session={session} />);
  const button = screen.getByRole("button", { name: "Change model" });
  await waitFor(() => expect(button).toBeEnabled());
  fireEvent.click(button);
  expect(await screen.findByRole("alert")).toHaveTextContent("Sign in and retry");
  expect(api.restart).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("menuitem", { name: "Retry model list" }));
  fireEvent.click(await screen.findByRole("menuitemradio", { name: /current-model/ }));
  expect(screen.queryByRole("menu")).not.toBeInTheDocument();
  expect(api.restart).not.toHaveBeenCalled();
  expect(button).toHaveFocus();
});

it("allows another harness when native resume is unverified and resets model scope", async () => {
  vi.mocked(api.restartStatus).mockResolvedValue({ can_restart: false, reason: "Cannot verify this conversation." });
  vi.mocked(api.restartOptions).mockResolvedValue({ ...options, after_turn: false, resume_restart: { available: false, reason: "Cannot verify this conversation." }, harnesses: options.harnesses.map(h => h.name === "codex" ? { ...h, available: false, reason: "Cannot verify this conversation." } : h) });
  vi.mocked(api.restart).mockResolvedValue({ status: "queued", context: "seeded_new_conversation", requested_harness: "claude-code" });
  render(<RestartControls session={session} />);
  fireEvent.click(screen.getByRole("button", { name: "Restart" }));
  await waitFor(() => expect(screen.getByLabelText("Harness")).toBeEnabled());
  expect(screen.getByRole("button", { name: "Restart now" })).toBeDisabled();
  fireEvent.change(screen.getByLabelText("Harness"), { target: { value: "claude-code" } });
  expect(screen.getByLabelText("Model")).toHaveValue("");
  expect(screen.queryByRole("option", { name: /current-model/ })).not.toBeInTheDocument();
  expect(screen.getByText(/New conversation in Claude Code, seeded from Codex/)).toBeVisible();
  fireEvent.change(screen.getByLabelText("Model"), { target: { value: "claude-exact" } });
  fireEvent.click(screen.getByRole("button", { name: "Switch to Claude Code" }));
  await waitFor(() => expect(api.restart).toHaveBeenCalledWith("a", "claude-exact", "claude-code"));
});

it("keeps unknown catalogs honest and prevents unavailable or draft-blocked switches", async () => {
  vi.mocked(api.restartOptions).mockResolvedValue({ ...options, draft_clear: false, harnesses: [options.harnesses[0], { ...options.harnesses[1], available: false, reason: "CLI not installed", models: [] }] });
  render(<RestartControls session={session} />);
  fireEvent.click(screen.getByRole("button", { name: "Restart" }));
  await waitFor(() => expect(screen.getByLabelText("Harness")).toBeEnabled());
  expect(screen.getByRole("button", { name: "Restart after this turn" })).toBeDisabled();
  fireEvent.change(screen.getByLabelText("Harness"), { target: { value: "claude-code" } });
  expect(screen.getByText("CLI not installed")).toBeVisible();
  expect(screen.getByRole("button", { name: "Switch after this turn" })).toBeDisabled();
  expect(api.restart).not.toHaveBeenCalled();
});

it("does not describe a completed harness switch as a continued conversation", async () => {
  vi.mocked(api.restartStatus).mockResolvedValue({ status: "completed", context: "seeded_new_conversation", source_harness: "claude-code", requested_harness: "codex" });
  render(<RestartControls session={session} />);
  expect(await screen.findByText(/New conversation in Codex, seeded from Claude Code/)).toBeVisible();
  expect(screen.queryByText(/conversation continued/)).not.toBeInTheDocument();
});

it("discards late option discovery when the selected card changes", async () => {
  let resolve!: (value: RestartOptions) => void;
  vi.mocked(api.restartOptions).mockImplementationOnce(() => new Promise(done => { resolve = done; }));
  const view = render(<RestartControls session={session} />);
  fireEvent.click(screen.getByRole("button", { name: "Restart" }));
  view.rerender(<RestartControls session={{ ...session, key: "b", label: "Other project" }} />);
  await act(async () => resolve(options));
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Restart" }));
  await waitFor(() => expect(screen.getByLabelText("Harness")).toBeEnabled());
  expect(screen.getByText("Other project · This Mac")).toBeVisible();
  expect(api.restart).not.toHaveBeenCalled();
});

it("closes stale confirmation if the same card changes harness elsewhere", async () => {
  const view = render(<RestartControls session={session} />);
  fireEvent.click(screen.getByRole("button", { name: "Restart" }));
  await waitFor(() => expect(screen.getByLabelText("Harness")).toBeEnabled());
  view.rerender(<RestartControls session={{ ...session, runtime: "claude-code" }} />);
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  expect(api.restart).not.toHaveBeenCalled();
});
