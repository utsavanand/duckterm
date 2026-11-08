import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { RuntimeSetupWizard } from "./RuntimeSetupWizard";
import { canSetUpAgents, type RuntimeSetupEvent, type RuntimeSetupStatus } from "./desktop";

const probe = vi.hoisted(() => ({ write: vi.fn(), clear: vi.fn() }));
vi.mock("./RuntimeSetupTerminal", async () => {
  const { forwardRef, useImperativeHandle } = await import("react");
  return { RuntimeSetupTerminal: forwardRef((props: { active: boolean; enabled: boolean; onInput: (data: string) => void }, ref) => {
    useImperativeHandle(ref, () => ({ write: probe.write, clear: probe.clear }), []);
    return <div hidden={!props.active} data-testid="setup-terminal"><button disabled={!props.enabled} onClick={() => props.onInput("eA==")}>Fixture terminal input</button></div>;
  }) };
});
vi.mock("./AiModelsModal", () => ({ AiModelsModal: ({ onClose }: { onClose: () => void }) => <section><h2>Existing AI model settings</h2><button onClick={onClose}>Close existing AI settings</button></section> }));
vi.mock("./edition", () => ({ useEdition: () => ({ id: "lite" }) }));

type Call = { operation: string; params: Record<string, string | boolean | number> };
const status: RuntimeSetupStatus = { protocol: 1, type: "status", runtime: "claude-code", installed: true, version: "2.1.304",
  probe: "ok", authentication: "signed_out", credential_method: "unknown", environment_api_key_configured: false };
let installed = true;
function deferred<T>() { let resolve!: (value: T) => void, reject!: (error: Error) => void; const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no; }); return { resolve, reject, promise }; }
function setup(handler?: (call: Call) => Promise<unknown>) {
  const request = vi.fn(async (value: unknown) => {
    const call = value as Call;
    if (handler) return handler(call);
    if (call.operation === "status") return status;
    if (call.operation === "complete") return { show_on_launch: false, completed: true, default_agent: call.params.agent };
    return {};
  });
  window.__rubbertermDesktop = { currentTarget: "local", canSetUpAgents: true, targets: [{ id: "local", name: "This Mac" }] };
  window.webkit = { messageHandlers: { runtimeSetup: { postMessage: request } } };
  return request;
}
async function mount() {
  const onClose = vi.fn();
  render(<RuntimeSetupWizard onClose={onClose} />);
  await screen.findByRole("button", { name: installed ? "Check setup" : "Set up Claude Code" });
  return onClose;
}
function event(request: string, payload: object, job = "native-job") {
  window.dispatchEvent(new CustomEvent("runtime-setup", { detail: { protocol: 1, job_id: job, request_id: request, ...payload } as RuntimeSetupEvent }));
}
async function install() {
  await mount(); fireEvent.click(screen.getByRole("button", { name: "Set up Claude Code" }));
  fireEvent.click(screen.getByRole("button", { name: "Install Claude Code" }));
}
beforeEach(() => {
  installed = true; probe.write.mockClear(); probe.clear.mockClear();
  Object.defineProperty(HTMLDialogElement.prototype, "showModal", { configurable: true, value: function (this: HTMLDialogElement) { this.open = true; } });
  Object.defineProperty(HTMLDialogElement.prototype, "close", { configurable: true, value: function (this: HTMLDialogElement) { this.open = false; } });
  vi.stubGlobal("requestAnimationFrame", (fn: FrameRequestCallback) => setTimeout(() => fn(0), 0));
  vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ runtimes: [
    { id: "claude-code", installed, version: installed ? "2.1.304" : null, probe: installed ? "ok" : "missing" },
    { id: "codex", installed: false, version: null, probe: "missing" },
  ] }))));
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); delete window.__rubbertermDesktop; delete window.webkit; });

it("does not turn installed discovery into authentication or start a probe without a click", async () => {
  const request = setup(); await mount();
  expect(screen.getByText("2.1.304")).toBeVisible();
  expect(screen.getAllByText("Sign-in not checked").length).toBeGreaterThan(0);
  expect(request).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Check setup" }));
  await screen.findByRole("heading", { name: "Sign in to Claude Code" });
  expect(request).toHaveBeenCalledWith({ operation: "status", params: { runtime: "claude-code", request_id: expect.any(String) } });
  expect(request.mock.calls.some(([call]) => (call as Call).operation === "start")).toBe(false);
});

it("correlates early events before start resolves and ignores other requests or jobs", async () => {
  installed = false; const pending = deferred<unknown>(); let id = "";
  const request = setup(async call => {
    if (call.operation === "start") { id = String(call.params.request_id); event(id, { type: "ready", runtime: "claude-code", action: "install" }); event(id, { type: "output", data: "b2s=" }); return pending.promise; }
    return {};
  });
  await install(); await waitFor(() => expect(probe.write).toHaveBeenCalledWith("b2s="));
  expect(screen.getByTestId("setup-terminal")).toBeVisible();
  expect(screen.getByRole("button", { name: "Fixture terminal input" })).toBeEnabled();
  await act(async () => { event("old-request", { type: "output", data: "SECRET" }); event(id, { type: "output", data: "SECRET" }, "other-job"); });
  expect(probe.write).toHaveBeenCalledTimes(1);
  fireEvent.click(screen.getByRole("button", { name: "Fixture terminal input" }));
  await waitFor(() => expect(request).toHaveBeenCalledWith({ operation: "input", params: { job_id: "native-job", data: "eA==" } }));
  await act(async () => { pending.resolve({ request_id: id, job_id: "native-job" }); });
  expect(request.mock.calls.filter(([call]) => (call as Call).operation === "start")).toHaveLength(1);
});

it("waits for cancellation cleanup before completion and never starts a replacement", async () => {
  installed = false; let id = "";
  const request = setup(async call => {
    if (call.operation === "start") { id = String(call.params.request_id); event(id, { type: "ready", runtime: "claude-code", action: "install" }); return { job_id: "native-job", request_id: id }; }
    if (call.operation === "complete") return { completed: true, show_on_launch: false };
    return {};
  });
  const close = await mount(); fireEvent.click(screen.getByRole("button", { name: "Set up Claude Code" }));
  fireEvent.click(screen.getByRole("button", { name: "Install Claude Code" }));
  await waitFor(() => expect(id).not.toBe(""));
  fireEvent.click(screen.getByRole("button", { name: "Set up later" }));
  await waitFor(() => expect(request).toHaveBeenCalledWith({ operation: "cancel", params: { request_id: id } }));
  expect(close).not.toHaveBeenCalled();
  expect(request.mock.calls.some(([call]) => (call as Call).operation === "complete")).toBe(false);
  expect(screen.getByRole("button", { name: "Fixture terminal input" })).toBeDisabled();
  await act(async () => { event(id, { type: "error", reason: "cancelled" }); });
  await waitFor(() => expect(close).toHaveBeenCalledOnce());
  expect(request).toHaveBeenCalledWith({ operation: "complete", params: { skipped: true } });
  expect(request.mock.calls.filter(([call]) => (call as Call).operation === "start")).toHaveLength(1);
});

it("cancels a matching status request and ignores a late signed-in response", async () => {
  const pending = deferred<unknown>(); let id = "";
  const request = setup(async call => { if (call.operation === "status") { id = String(call.params.request_id); return pending.promise; } return { completed: true, show_on_launch: false }; });
  const close = await mount(); fireEvent.click(screen.getByRole("button", { name: "Check setup" }));
  await waitFor(() => expect(id).not.toBe(""));
  fireEvent.click(screen.getByRole("button", { name: "Set up later" }));
  await waitFor(() => expect(request).toHaveBeenCalledWith({ operation: "cancel", params: { request_id: id } }));
  expect(close).not.toHaveBeenCalled();
  await act(async () => { pending.resolve({ ...status, authentication: "signed_in" }); });
  await waitFor(() => expect(close).toHaveBeenCalledOnce());
  expect(screen.queryByText("Claude Code is ready")).toBeNull();
});

it("closing before the first frame is painted does not launch or cancel a nonexistent helper", async () => {
  installed = false; let frame!: FrameRequestCallback;
  vi.stubGlobal("requestAnimationFrame", (callback: FrameRequestCallback) => { frame = callback; return 1; });
  const request = setup(); const close = await mount();
  fireEvent.click(screen.getByRole("button", { name: "Set up Claude Code" }));
  fireEvent.click(screen.getByRole("button", { name: "Install Claude Code" }));
  fireEvent.click(screen.getByRole("button", { name: "Close agent setup" }));
  await waitFor(() => expect(close).toHaveBeenCalledOnce());
  await act(async () => { frame(0); });
  expect(request).not.toHaveBeenCalled();
});

it("uses only a native selection handle for checking and completion", async () => {
  installed = false;
  const request = setup(async call => {
    if (call.operation === "choose-executable") return { selection_handle: "native-selection", display_name: "claude" };
    if (call.operation === "status") return { ...status, authentication: "signed_in", credential_method: "account" };
    return { completed: true, show_on_launch: false };
  });
  await mount(); fireEvent.click(screen.getByRole("button", { name: "Set up Claude Code" }));
  fireEvent.click(screen.getByRole("button", { name: "Already installed?" }));
  fireEvent.click(screen.getByRole("button", { name: "Choose executable…" }));
  await waitFor(() => expect(screen.getByLabelText("Executable")).toHaveValue("claude"));
  expect(screen.getByLabelText("Executable")).toHaveAttribute("readonly");
  fireEvent.click(screen.getByRole("button", { name: "Check installation" }));
  await screen.findByText("Claude Code is ready");
  expect(request).toHaveBeenCalledWith({ operation: "status", params: { runtime: "claude-code", request_id: expect.any(String), selection_handle: "native-selection" } });
  fireEvent.click(screen.getByRole("button", { name: "Continue" }));
  fireEvent.click(screen.getByRole("button", { name: "Open DuckTerm" }));
  await waitFor(() => expect(request).toHaveBeenCalledWith({ operation: "complete", params: { skipped: false, agent: "claude", selection_handle: "native-selection" } }));
});

it("rechecks successful actions, but a failed action exposes no raw exception or automatic retry", async () => {
  installed = false; let id = "";
  const request = setup(async call => {
    if (call.operation === "start") { id = String(call.params.request_id); return { job_id: "native-job", request_id: id }; }
    if (call.operation === "status") return status;
    return {};
  });
  await install(); await waitFor(() => expect(id).not.toBe(""));
  await act(async () => { event(id, { type: "ready", runtime: "claude-code", action: "install" }); event(id, { type: "exited", exit_code: 0, cancelled: false }); });
  await screen.findByRole("heading", { name: "Sign in to Claude Code" });
  request.mockRejectedValueOnce(new Error("SECRET provider auth output"));
  fireEvent.click(screen.getByRole("button", { name: "Sign in" }));
  await screen.findByRole("heading", { name: "Setup didn’t finish" });
  expect(document.body).not.toHaveTextContent("SECRET");
  expect(request.mock.calls.filter(([call]) => (call as Call).operation === "start")).toHaveLength(2);
});

it("native-free and remote dashboards offer manual setup without a helper fallback", async () => {
  const request = setup(); window.__rubbertermDesktop!.currentTarget = "remote";
  expect(canSetUpAgents()).toBe(false);
  render(<RuntimeSetupWizard onClose={vi.fn()} />);
  fireEvent.click(await screen.findByRole("button", { name: "Manual instructions" }));
  expect(screen.getByRole("heading", { name: "Set up Claude Code yourself" })).toBeVisible();
  expect(request).not.toHaveBeenCalled();
  expect(fetch).toHaveBeenCalledTimes(1);
});

it("the optional AI step reuses existing settings and never saves a hidden provider or paid model", async () => {
  const request = setup(); const close = await mount();
  fireEvent.click(screen.getByRole("radio", { name: /Another command/ }));
  fireEvent.change(screen.getByLabelText("Command"), { target: { value: "zsh" } });
  fireEvent.click(screen.getByRole("button", { name: "Continue" }));
  fireEvent.click(screen.getByRole("radio", { name: /Use my API key or endpoint/ }));
  fireEvent.click(screen.getByRole("button", { name: "Configure API key…" }));
  expect(screen.getByRole("heading", { name: "Existing AI model settings" })).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "Close existing AI settings" }));
  expect(screen.getByRole("heading", { name: "DuckTerm AI · Optional" })).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "Open DuckTerm" }));
  await waitFor(() => expect(close).toHaveBeenCalledOnce());
  expect(request).toHaveBeenCalledExactlyOnceWith({ operation: "complete", params: { skipped: false, agent: "custom", command: "zsh" } });
  expect(fetch).toHaveBeenCalledTimes(1);
});

it("unmount cancels only its own request and drops subsequent output", async () => {
  installed = false; let id = "";
  const request = setup(async call => { if (call.operation === "start") { id = String(call.params.request_id); return { job_id: "native-job", request_id: id }; } return {}; });
  await install(); await waitFor(() => expect(id).not.toBe(""));
  cleanup();
  expect(request).toHaveBeenCalledWith({ operation: "cancel", params: { request_id: id } });
  await act(async () => { event(id, { type: "output", data: "SECRET" }); });
  expect(probe.write).not.toHaveBeenCalled();
});

it("a host switch closes local setup and ignores stale authentication replies", async () => {
  const pending = deferred<unknown>();
  setup(async call => call.operation === "status" ? pending.promise : {});
  const close = await mount(); fireEvent.click(screen.getByRole("button", { name: "Check setup" }));
  await screen.findByRole("heading", { name: "Checking Claude Code" });
  await act(async () => {
    window.__rubbertermDesktop!.currentTarget = "remote";
    window.dispatchEvent(new Event("desktop-targets-changed"));
    pending.resolve({ ...status, authentication: "signed_in" });
  });
  expect(close).toHaveBeenCalledOnce();
  expect(screen.queryByText("Claude Code is ready")).toBeNull();
});

it("checks a saved executable instead of mistaking missing PATH discovery for a missing installation", async () => {
  installed = false;
  const request = setup();
  window.__rubbertermDesktop!.agentSetup = { completed: true, show_on_launch: false, default_agent: "claude", default_command: "/private/custom/claude" };
  render(<RuntimeSetupWizard onClose={vi.fn()} />);
  await screen.findByText("Saved installation");
  fireEvent.click(screen.getByRole("button", { name: "Check setup" }));
  await screen.findByRole("heading", { name: "Sign in to Claude Code" });
  expect(request).toHaveBeenCalledExactlyOnceWith({ operation: "status", params: { runtime: "claude-code", request_id: expect.any(String) } });
  expect(screen.queryByRole("heading", { name: "Install Claude Code" })).toBeNull();
});
