import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { LaunchModal } from "./LaunchModal";

vi.mock("./api", () => ({ api: {
  zshThemes: vi.fn().mockResolvedValue({ themes: [] }),
  browse: vi.fn().mockResolvedValue({ path: "/Users/test", parent: null, is_git: false, entries: [] }),
} }));
afterEach(() => {
  cleanup();
  delete window.__rubbertermDesktop;
  delete window.webkit;
});

function setup(request: (message: unknown) => Promise<unknown>) {
  const switchHost = vi.fn();
  window.webkit = { messageHandlers: {
    remoteSession: { postMessage: switchHost },
    launchRequest: { postMessage: request },
  } };
  window.__rubbertermDesktop = {
    currentTarget: "local",
    targets: [{ id: "local", name: "This Mac" }, { id: "dev", name: "Remote — dev" }],
  };
  return switchHost;
}

it("keeps the same form and dashboard until a remote launch succeeds", async () => {
  const request = vi.fn(async (raw: unknown) => {
    const message = raw as { operation: string };
    if (message.operation === "themes") return { themes: [] };
    if (message.operation === "launch") return { session_key: "remote-task" };
    return { path: "/home/test/project", parent: null, is_git: false, entries: [] };
  });
  const switchHost = setup(request);
  const close = vi.fn();
  await act(async () => { render(<LaunchModal onClose={close} group="local-folder" />); });
  const name = screen.getByPlaceholderText("e.g. login refactor");
  fireEvent.change(name, { target: { value: "My task" } });
  fireEvent.click(screen.getByText("Codex"));
  await act(async () => { fireEvent.change(screen.getByRole("combobox", { name: "Run on" }), { target: { value: "dev" } }); });
  expect(switchHost).not.toHaveBeenCalled();
  expect(screen.getByDisplayValue("My task")).toBe(name);
  expect(window.__rubbertermDesktop?.currentTarget).toBe("local");
  fireEvent.click(screen.getByText("Browse…"));
  await screen.findByText("Use this folder");
  fireEvent.click(screen.getByText("Use this folder"));
  fireEvent.click(screen.getByText("Launch"));
  await waitFor(() => expect(switchHost).toHaveBeenCalledWith({ action: "launch", target: "dev", draft: {} }));
  expect(request).toHaveBeenCalledWith({ target: "dev", operation: "launch", params: {
    command: "codex", name: "My task", cwd: "/home/test/project",
  } });
  expect(close).toHaveBeenCalledOnce();
});

it("ignores remote folder replies after returning to This Mac and lets Cancel close without switching", async () => {
  let resolveBrowse!: (value: unknown) => void;
  const switchHost = setup(async (raw: unknown) => {
    if ((raw as { operation: string }).operation === "themes") return { themes: [] };
    return await new Promise((resolve) => { resolveBrowse = resolve; });
  });
  const close = vi.fn();
  await act(async () => { render(<LaunchModal onClose={close} />); });
  await act(async () => { fireEvent.change(screen.getByRole("combobox", { name: "Run on" }), { target: { value: "dev" } }); });
  fireEvent.click(screen.getByText("Browse…"));
  await act(async () => { fireEvent.change(screen.getByRole("combobox", { name: "Run on" }), { target: { value: "local" } }); });
  await act(async () => { resolveBrowse({ path: "/remote/stale", parent: null, entries: [], is_git: false }); });
  expect(screen.queryByText("/remote/stale")).not.toBeInTheDocument();
  fireEvent.click(screen.getByText("Browse…"));
  expect(await screen.findByText("/Users/test")).toBeVisible();
  fireEvent.click(screen.getAllByRole("button", { name: "Cancel" }).at(-1)!);
  expect(close).toHaveBeenCalledOnce();
  expect(switchHost).not.toHaveBeenCalled();
});

it("shows a connection error inside the form without changing dashboards", async () => {
  const switchHost = setup(async () => { throw new Error("SSH unavailable"); });
  await act(async () => { render(<LaunchModal onClose={() => undefined} />); });
  await act(async () => { fireEvent.change(screen.getByRole("combobox", { name: "Run on" }), { target: { value: "dev" } }); });
  fireEvent.click(screen.getByText("Browse…"));
  expect(await screen.findByText("SSH unavailable")).toBeVisible();
  expect(screen.getByText("Retry")).toBeVisible();
  expect(switchHost).not.toHaveBeenCalled();
});
