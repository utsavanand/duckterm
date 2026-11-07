import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { api } from "./api";
import { ForkModal } from "./ForkModal";
import type { SessionView } from "./types";

const { toast } = vi.hoisted(() => ({ toast: vi.fn() }));
vi.mock("./api", () => ({
  api: { fork: vi.fn(), promote: vi.fn(), forkConversation: vi.fn() },
}));
vi.mock("./ui", async importOriginal => ({
  ...await importOriginal<typeof import("./ui")>(), useToast: () => toast,
}));
afterEach(() => { cleanup(); vi.resetAllMocks(); });

function open(overrides: Partial<SessionView> = {}) {
  const session = {
    key: "test-parent", label: "Fork test", runtime: "codex", state: "idle",
    startedAt: 1, cwd: "/test/project", branch: "feature", test: true,
    ...overrides,
  } as SessionView;
  const close = vi.fn();
  render(<ForkModal session={session} onClose={close} />);
  return { session, close };
}

it.each([true, false])("sends Codex's explicit carry-context choice (%s) with its own command", async carry => {
  vi.mocked(api.fork).mockResolvedValue({
    session_key: "test-child", branch: "feature-fork",
    carried_context: carry,
  });
  const { session, close } = open();
  const checkbox = screen.getByRole("checkbox", { name: /Carry the conversation/ });
  expect(checkbox).toBeChecked();
  expect(screen.getByDisplayValue("codex")).toBeVisible();
  expect(screen.getByRole("radio", { name: /Conversation only/ })).toBeDisabled();
  if (!carry) fireEvent.click(checkbox);
  fireEvent.click(screen.getByRole("button", { name: "Create fork" }));
  await waitFor(() => expect(close).toHaveBeenCalledOnce());
  expect(api.fork).toHaveBeenCalledExactlyOnceWith(session.key, {
    command: "codex", branch: "feature-fork", in_terminal: false, carry_context: carry,
  });
  expect(api.promote).not.toHaveBeenCalled();
  expect(api.forkConversation).not.toHaveBeenCalled();
});

it("reports a fresh Codex fork when the backend cannot find a conversation", async () => {
  vi.mocked(api.fork).mockResolvedValue({
    session_key: "test-child", branch: "feature-fork", carried_context: false,
  });
  const { close } = open();
  fireEvent.click(screen.getByRole("button", { name: "Create fork" }));
  await waitFor(() => expect(close).toHaveBeenCalledOnce());
  expect(toast).toHaveBeenCalledWith("Fork running on feature-fork — no conversation to carry yet, started fresh");
});

it.each(["claude-code", "copilot"])("preserves worktree continuity for %s", runtime => {
  open({ runtime });
  expect(screen.getByRole("checkbox", { name: /Carry the conversation/ })).toBeChecked();
});

it.each(["generic", "opencode"])("does not offer unsupported %s context carry", runtime => {
  open({ runtime });
  expect(screen.queryByRole("checkbox", { name: /Carry the conversation/ })).not.toBeInTheDocument();
});

it("does not promise a child conversation when promoting an in-place session", async () => {
  vi.mocked(api.promote).mockResolvedValue({
    branch: "new-work", worktree: "/test/fork",
  });
  const { session, close } = open({ branch: undefined });
  expect(screen.queryByRole("checkbox", { name: /Carry the conversation/ })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Create fork" }));
  await waitFor(() => expect(close).toHaveBeenCalledOnce());
  expect(api.promote).toHaveBeenCalledExactlyOnceWith(session.key, { branch: undefined });
  expect(api.fork).not.toHaveBeenCalled();
});

it("keeps a failed fork open without silently dropping requested context", async () => {
  vi.mocked(api.fork).mockRejectedValue(new Error("Parent is unavailable"));
  const { close } = open();
  fireEvent.click(screen.getByRole("button", { name: "Create fork" }));
  await waitFor(() => expect(toast).toHaveBeenCalledWith("Fork failed: Parent is unavailable", "err"));
  expect(close).not.toHaveBeenCalled();
  expect(screen.getByRole("checkbox", { name: /Carry the conversation/ })).toBeChecked();
  expect(screen.getByRole("button", { name: "Create fork" })).toBeEnabled();
});
