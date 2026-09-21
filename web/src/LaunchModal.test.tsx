import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { LaunchModal } from "./LaunchModal";

vi.mock("./api", () => ({ api: { zshThemes: vi.fn().mockResolvedValue({ themes: [] }) } }));
afterEach(() => {
  cleanup();
  delete window.__rubbertermDesktop;
  delete window.webkit;
});

it("offers the remote computer inside New Session and carries only portable form text", async () => {
  const postMessage = vi.fn();
  window.webkit = { messageHandlers: { remoteSession: { postMessage } } };
  window.__rubbertermDesktop = {
    currentTarget: "local",
    targets: [{ id: "local", name: "This Mac" }, { id: "dev", name: "Remote — dev" }],
  };
  await act(async () => { render(<LaunchModal onClose={() => undefined} group="local-folder" />); });
  fireEvent.click(screen.getByText("Codex"));
  fireEvent.change(screen.getByPlaceholderText("e.g. login refactor"), { target: { value: "My task" } });
  fireEvent.change(screen.getByPlaceholderText("add a healthcheck endpoint"), { target: { value: "Fix it" } });
  fireEvent.change(screen.getByRole("combobox", { name: "Run on" }), { target: { value: "dev" } });
  expect(postMessage).toHaveBeenCalledWith({
    action: "launch", target: "dev",
    draft: { agent: "codex", command: "codex", name: "My task", prompt: "Fix it" },
  });
});

it("restores the task on the destination but asks for a folder on that computer", async () => {
  window.__rubbertermDesktop = {
    currentTarget: "dev",
    targets: [{ id: "local", name: "This Mac" }, { id: "dev", name: "Remote — dev" }],
    draft: { agent: "codex", command: "codex", name: "My task", prompt: "Fix it" },
  };
  await act(async () => { render(<LaunchModal onClose={() => undefined} />); });
  expect(screen.getByRole("combobox", { name: "Run on" })).toHaveValue("dev");
  expect(screen.getByDisplayValue("My task")).toBeVisible();
  expect(screen.getByDisplayValue("Fix it")).toBeVisible();
  expect(screen.getByText("Browse…")).toBeVisible();
  expect(screen.getByText(/Keeps running remotely/)).toBeVisible();
});
