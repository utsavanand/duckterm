import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { api } from "./api";
import { LaunchModal } from "./LaunchModal";
import { openCodeCommand, validOpenCodeModel, type OpenCodeLaunchOptions } from "./OpenCodeOptions";

vi.mock("./api", () => ({ api: {
  openCodeOptions: vi.fn(), launch: vi.fn(), setGroup: vi.fn(),
  zshThemes: vi.fn().mockResolvedValue({ themes: [] }),
  browse: vi.fn().mockResolvedValue({ path: "/synthetic/project", parent: null, is_git: false, entries: [] }),
} }));
const available: OpenCodeLaunchOptions = {
  launch: { available: true },
  models: [{ id: "local/org/model", label: "Local model", configured: true }, { id: "other/model", label: "Not configured", configured: false }],
  capabilities: [{ id: "fork", label: "Fork conversation", available: false, reason: "Not supported yet." }],
};
beforeEach(() => {
  vi.mocked(api.openCodeOptions).mockReset().mockResolvedValue(available);
  vi.mocked(api.launch).mockReset().mockResolvedValue({ session_key: "fixture" });
});
afterEach(() => { cleanup(); delete window.__rubbertermDesktop; delete window.webkit; });

async function form() {
  await act(async () => { render(<LaunchModal folders={[]} onClose={vi.fn()} onCreated={vi.fn()} />); });
  await act(async () => { fireEvent.click(screen.getByRole("button", { name: "OpenCode" })); });
}
async function folder() {
  fireEvent.click(screen.getByText("Browse…"));
  fireEvent.click(await screen.findByText("Use this folder"));
}

it("keeps the default optional and passes custom model IDs literally in the existing launch flow", async () => {
  await form();
  await folder();
  expect(screen.getByLabelText("OpenCode model")).toHaveValue("");
  expect(screen.getByRole("option", { name: "Local model", hidden: true })).toHaveValue("local/org/model");
  expect(screen.queryByRole("option", { name: "Not configured", hidden: true })).toBeNull();
  const model = "local/org/model:'quoted';$(literal)";
  fireEvent.change(screen.getByLabelText("OpenCode model"), { target: { value: model } });
  fireEvent.click(screen.getByRole("button", { name: "Launch" }));
  await waitFor(() => expect(api.launch).toHaveBeenCalledWith(expect.objectContaining({ command: openCodeCommand(model), cwd: "/synthetic/project", in_terminal: false })));
});

it("blocks malformed model IDs and does not apply OpenCode models to another agent", async () => {
  await form(); await folder();
  fireEvent.change(screen.getByLabelText("OpenCode model"), { target: { value: "missing-provider" } });
  expect(screen.getByRole("button", { name: "Launch" })).toBeDisabled();
  expect(screen.getByRole("alert")).toHaveTextContent("provider/model");
  fireEvent.click(screen.getByRole("button", { name: "Codex" }));
  fireEvent.click(screen.getByRole("button", { name: "Launch" }));
  await waitFor(() => expect(api.launch).toHaveBeenCalledWith(expect.objectContaining({ command: "codex" })));
  expect(openCodeCommand("")).toBe("opencode");
  expect(validOpenCodeModel("provider/ spaced")).toBe(false);
  expect(validOpenCodeModel("-flag/model")).toBe(false);
});

it("explains unavailable capabilities and failed support checks without allowing a generic launch", async () => {
  vi.mocked(api.openCodeOptions).mockRejectedValueOnce(new Error("Connection unavailable"));
  await form();
  expect(screen.getByRole("button", { name: "Launch" })).toBeDisabled();
  expect(screen.getByRole("alert")).toHaveTextContent("Connection unavailable");
  vi.mocked(api.openCodeOptions).mockResolvedValue({ ...available, launch: { available: false, reason: "Update this computer first." } });
  fireEvent.click(screen.getByText("Retry OpenCode check"));
  await screen.findByText("Update this computer first.");
  fireEvent.click(screen.getByText("OpenCode support"));
  expect(screen.getByText("Fork conversation: Unavailable.")).toBeVisible();
  expect(screen.getByRole("button", { name: "Launch" })).toBeDisabled();
  expect(api.launch).not.toHaveBeenCalled();
});

it("isolates model drafts by computer and ignores a stale remote support response", async () => {
  let resolveRemote!: (value: OpenCodeLaunchOptions) => void;
  const bridge = vi.fn(async (raw: unknown) => {
    const { operation } = raw as { operation: string };
    if (operation === "opencode-options") return new Promise<OpenCodeLaunchOptions>(resolve => { resolveRemote = resolve; });
    return { themes: [] };
  });
  window.__rubbertermDesktop = { currentTarget: "local", targets: [{ id: "local", name: "This Mac" }, { id: "dev", name: "Remote — dev" }] };
  window.webkit = { messageHandlers: { launchRequest: { postMessage: bridge } } };
  await form();
  fireEvent.change(screen.getByLabelText("OpenCode model"), { target: { value: "mac/model" } });
  await act(async () => { fireEvent.change(screen.getByLabelText("Run on"), { target: { value: "dev" } }); });
  fireEvent.click(screen.getByText("Other project options"));
  fireEvent.change(screen.getByLabelText("Repository URL"), { target: { value: "https://github.com/fixture/project.git" } });
  fireEvent.click(screen.getByRole("button", { name: "Next →" }));
  expect(screen.getByLabelText("OpenCode model")).toHaveValue("");
  expect(screen.getByRole("button", { name: "Next →" })).toBeDisabled();
  fireEvent.change(screen.getByLabelText("OpenCode model"), { target: { value: "remote/model" } });
  fireEvent.click(screen.getByRole("button", { name: "Back" }));
  await act(async () => { fireEvent.change(screen.getByLabelText("Run on"), { target: { value: "local" } }); });
  await act(async () => { resolveRemote({ ...available, launch: { available: false, reason: "Stale remote result" } }); });
  expect(screen.getByLabelText("OpenCode model")).toHaveValue("mac/model");
  expect(screen.queryByText("Stale remote result")).toBeNull();
  expect(bridge).toHaveBeenCalledWith({ target: "dev", operation: "opencode-options", params: {} });
});

it("rejects malformed destination metadata instead of rendering it or enabling launch", async () => {
  vi.mocked(api.openCodeOptions).mockResolvedValue({ ...available, models: [null] } as unknown as OpenCodeLaunchOptions);
  await form();
  expect(screen.getByRole("alert")).toHaveTextContent("did not report OpenCode support");
  expect(screen.getByRole("button", { name: "Launch" })).toBeDisabled();
});

it("reselecting OpenCode blocks launch until fresh support arrives", async () => {
  await form(); await folder();
  expect(screen.getByRole("button", { name: "Launch" })).toBeEnabled();
  fireEvent.click(screen.getByRole("button", { name: "Codex" }));
  let finish!: (options: OpenCodeLaunchOptions) => void;
  vi.mocked(api.openCodeOptions).mockImplementationOnce(() => new Promise(resolve => { finish = resolve; }));
  await act(async () => { fireEvent.click(screen.getByRole("button", { name: "OpenCode" })); });
  expect(screen.getByRole("button", { name: "Launch" })).toBeDisabled();
  await act(async () => { finish(available); });
  expect(screen.getByRole("button", { name: "Launch" })).toBeEnabled();
});

it("returning to a computer cannot reuse old support while its new request is pending", async () => {
  let remoteReply!: (options: OpenCodeLaunchOptions) => void;
  window.__rubbertermDesktop = { currentTarget: "local", targets: [{ id: "local", name: "This Mac" }, { id: "dev", name: "Remote — dev" }] };
  window.webkit = { messageHandlers: { launchRequest: { postMessage: async (raw: unknown) => {
    if ((raw as { operation: string }).operation === "opencode-options") return new Promise<OpenCodeLaunchOptions>(resolve => { remoteReply = resolve; });
    return { themes: [] };
  } } } };
  await form();
  let localReply!: (options: OpenCodeLaunchOptions) => void;
  vi.mocked(api.openCodeOptions).mockImplementationOnce(() => new Promise(resolve => { localReply = resolve; }));
  await act(async () => { fireEvent.change(screen.getByLabelText("Run on"), { target: { value: "dev" } }); });
  await act(async () => { fireEvent.change(screen.getByLabelText("Run on"), { target: { value: "local" } }); });
  expect(screen.getByRole("button", { name: "Launch" })).toBeDisabled();
  await act(async () => { remoteReply(available); });
  expect(screen.getByRole("button", { name: "Launch" })).toBeDisabled();
  await act(async () => { localReply({ ...available, launch: { available: false, reason: "Destination changed" } }); });
  expect(screen.getByRole("button", { name: "Launch" })).toBeDisabled();
  expect(screen.getByText("Destination changed")).toBeVisible();
});
