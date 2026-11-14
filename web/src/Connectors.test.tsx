import { act, fireEvent, render, screen, waitFor, cleanup } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { Connectors } from "./Connectors";
import { api, Connector } from "./api";

vi.mock("./api", () => ({ api: { connectors: vi.fn(), enableConnector: vi.fn(), disableConnector: vi.fn(), forgetConnector: vi.fn(), verifyConnector: vi.fn() } }));
afterEach(() => { cleanup(); vi.resetAllMocks(); });
beforeEach(() => { vi.mocked(api.verifyConnector).mockResolvedValue({ name: "porkbun", ok: true, tools: 3, detail: null }); });

const row: Connector = { name: "porkbun", title: "Porkbun", description: "DNS", credential: null, stored_secret: false, identity: null, sources: ["stored"], write_access: false, enabled: false, installed: {}, ready: false, detail: null, managed: false, revoke_url: "https://porkbun.com/account/api", last_used: null, use_count: 0, harnesses: ["claude-code", "codex"], harnesses_present: { "claude-code": true, codex: true } };

it("requires a separate write opt-in and sends read-only by default", async () => {
  vi.mocked(api.connectors).mockResolvedValue({ connectors: [row] });
  vi.mocked(api.enableConnector).mockResolvedValue({ ...row, enabled: true, credential: "stored" });
  render(<Connectors />);
  fireEvent.click(await screen.findByLabelText(/^Configure /));
  expect(screen.getByLabelText(/Allow changes to domains/)).not.toBeChecked();
  fireEvent.change(screen.getByLabelText("Porkbun API key"), { target: { value: "test-key" } });
  fireEvent.change(screen.getByLabelText("Porkbun secret key"), { target: { value: "test-secret" } });
  fireEvent.click(screen.getByText("Verify and enable"));
  await waitFor(() => expect(api.enableConnector).toHaveBeenCalledWith("porkbun", "test-key", "test-secret", "stored", false, "", ["claude-code", "codex"]));
  expect(await screen.findByLabelText(/^Configure /)).toBeVisible();
  expect(screen.queryByLabelText("Porkbun API key")).toBeNull();
});

it("disable preserves credentials and does not call forget", async () => {
  vi.mocked(api.connectors).mockResolvedValue({ connectors: [{ ...row, credential: "stored", stored_secret: true, enabled: true }] });
  vi.mocked(api.disableConnector).mockResolvedValue({ ...row, credential: "stored", stored_secret: true, enabled: false });
  render(<Connectors />);
  fireEvent.click(await screen.findByLabelText(/^Configure /));
  fireEvent.click(screen.getByLabelText("Porkbun available to agents"));
  await waitFor(() => expect(api.disableConnector).toHaveBeenCalledWith("porkbun", ""));
  expect(api.forgetConnector).not.toHaveBeenCalled();
  expect(await screen.findByText("Forget stored credentials")).toBeVisible();
});

it("hides Forget and Revoke when the credential is a provider CLI login", async () => {
  // Forget disables the connector and deletes a secret that was never stored,
  // so offering it for a gh-cli login breaks a working connector for nothing.
  const github: Connector = { ...row, name: "github", title: "GitHub", description: "Repos", credential: "gh-cli", stored_secret: false, enabled: true, sources: ["gh-cli", "stored"], revoke_url: "https://github.com/settings/applications" };
  vi.mocked(api.connectors).mockResolvedValue({ connectors: [github] });
  render(<Connectors />);
  fireEvent.click(await screen.findByLabelText(/^Configure /));
  expect(screen.getByLabelText("GitHub credential source")).toBeVisible();
  expect(screen.queryByText("Forget stored credentials")).toBeNull();
  expect(screen.queryByText(/Revoke at GitHub/)).toBeNull();
  expect(screen.getByText(/Duckterm stores no credential for it/)).toBeVisible();
});

it("keeps Forget and Revoke when Duckterm holds the credential itself", async () => {
  vi.mocked(api.connectors).mockResolvedValue({ connectors: [{ ...row, credential: "stored", stored_secret: true, enabled: true }] });
  render(<Connectors />);
  fireEvent.click(await screen.findByLabelText(/^Configure /));
  expect(screen.getByText("Forget stored credentials")).toBeVisible();
  expect(screen.getByText(/Revoke at Porkbun/)).toBeVisible();
});

it("can still forget a secret that outlived its connection", async () => {
  // Saved, then never enabled: credential is null but the key is still on disk.
  vi.mocked(api.connectors).mockResolvedValue({ connectors: [{ ...row, credential: null, stored_secret: true }] });
  render(<Connectors />);
  fireEvent.click(await screen.findByLabelText(/^Configure /));
  expect(screen.getByText("Forget stored credentials")).toBeVisible();
  // No source yet, so there is nothing to switch off.
  expect(screen.queryByLabelText("Porkbun available to agents")).toBeNull();
});

it("does not expose secret administration on the hosted agent dashboard", async () => {
  vi.mocked(api.connectors).mockResolvedValue({ connectors: [{ ...row, managed: true }] });
  render(<Connectors />);
  expect(await screen.findByText("Managed by your connector host")).toBeVisible();
  expect(screen.queryByLabelText(/^Configure /)).toBeNull();
});

it("picks up a connector that appears while the window regains focus", async () => {
  const porkbun: Connector = { ...row, name: "porkbun", title: "Porkbun", sources: ["stored"] };
  vi.mocked(api.connectors).mockResolvedValueOnce({ connectors: [] }).mockResolvedValue({ connectors: [porkbun] });
  render(<Connectors />);
  await screen.findByText(/^Connectors · /);
  fireEvent(window, new Event("focus"));
  expect(await screen.findByText("Porkbun")).toBeVisible();
  fireEvent.click(screen.getByText("Refresh"));
  await waitFor(() => expect(api.connectors).toHaveBeenCalledTimes(3));
});

it("allows a shared relay to register access without accepting provider secrets", async () => {
  const relay: Connector = { ...row, managed: true, hosted: false, ready: true };
  vi.mocked(api.connectors).mockResolvedValue({ connectors: [relay] });
  vi.mocked(api.enableConnector).mockResolvedValue({ ...relay, enabled: true });
  render(<Connectors />);
  fireEvent.click(await screen.findByText("Connect"));
  // A managed relay enables without opening the form, so it sends no harness
  // choice and the stored one stands.
  await waitFor(() => expect(api.enableConnector).toHaveBeenCalledWith("porkbun", undefined, undefined, "", false, "", undefined));
  expect(screen.queryByLabelText("Porkbun API key")).toBeNull();
});

it("claims verification only after a check, and reports the tool count it proved", async () => {
  const live: Connector = { ...row, enabled: true, credential: "stored", ready: true };
  vi.mocked(api.connectors).mockResolvedValue({ connectors: [live] });
  vi.mocked(api.verifyConnector).mockResolvedValue({ name: "porkbun", ok: true, tools: 25, detail: null });
  render(<Connectors />);
  // No click: an unverified row would only be able to claim a file exists.
  expect(await screen.findByText(/25 tools/)).toBeVisible();
  fireEvent.click(screen.getByLabelText(/^Configure /));
  expect(screen.getByText(/✓ Verified — 25 tools available/)).toBeVisible();
});

it("shows the server's own error when verification fails", async () => {
  const live: Connector = { ...row, enabled: true, credential: "stored", ready: true };
  vi.mocked(api.connectors).mockResolvedValue({ connectors: [live] });
  vi.mocked(api.verifyConnector).mockResolvedValue({
    name: "porkbun", ok: false, tools: 0, detail: "The connector did not start: uvx not found",
  });
  render(<Connectors />);
  // A failing automatic check must surface on the row, not stay silent.
  expect(await screen.findByText(/Not working — The connector did not start: uvx not found/)).toBeVisible();
});

it("says use is unrecorded rather than claiming a connector is unused", async () => {
  const unused: Connector = { ...row, enabled: true, credential: "stored", last_used: null, use_count: 0 };
  const used: Connector = {
    ...row, name: "github", title: "GitHub", enabled: true, credential: "gh-cli",
    last_used: Date.now() - 2 * 3600 * 1000, use_count: 90,
  };
  vi.mocked(api.connectors).mockResolvedValue({ connectors: [unused, used] });
  render(<Connectors />);
  expect(await screen.findByText(/No recorded use/)).toBeVisible();
  expect(screen.getByText(/Used 2h ago · 90 calls/)).toBeVisible();
});

it("sends only the agents left checked, and refuses to send none", async () => {
  vi.mocked(api.connectors).mockResolvedValue({ connectors: [row] });
  vi.mocked(api.enableConnector).mockResolvedValue({ ...row, enabled: true, credential: "stored", harnesses: ["claude-code"] });
  render(<Connectors />);
  fireEvent.click(await screen.findByLabelText(/^Configure /));
  fireEvent.change(screen.getByLabelText("Porkbun API key"), { target: { value: "k" } });
  fireEvent.change(screen.getByLabelText("Porkbun secret key"), { target: { value: "s" } });
  fireEvent.click(screen.getByLabelText("Codex"));
  fireEvent.click(screen.getByText("Verify and enable"));
  await waitFor(() => expect(api.enableConnector).toHaveBeenCalledWith("porkbun", "k", "s", "stored", false, "", ["claude-code"]));
});

it("cannot submit a connector with no agent selected", async () => {
  vi.mocked(api.connectors).mockResolvedValue({ connectors: [row] });
  render(<Connectors />);
  fireEvent.click(await screen.findByLabelText(/^Configure /));
  fireEvent.change(screen.getByLabelText("Porkbun API key"), { target: { value: "k" } });
  fireEvent.change(screen.getByLabelText("Porkbun secret key"), { target: { value: "s" } });
  fireEvent.click(screen.getByLabelText("Codex"));
  fireEvent.click(screen.getByLabelText("Claude Code"));
  expect(screen.getByText("Verify and enable")).toBeDisabled();
  expect(api.enableConnector).not.toHaveBeenCalled();
});

it("names the agents a connector reaches instead of reporting a config entry", async () => {
  const live: Connector = { ...row, enabled: true, credential: "stored", harnesses: ["claude-code"] };
  vi.mocked(api.connectors).mockResolvedValue({ connectors: [live] });
  render(<Connectors />);
  fireEvent.click(await screen.findByLabelText(/^Configure /));
  expect(screen.getByText("Available to Claude Code · not Codex")).toBeVisible();
});

it("says an agent is missing rather than implying the connector serves it", async () => {
  const live: Connector = {
    ...row, enabled: true, credential: "stored",
    harnesses: ["claude-code", "codex"],
    harnesses_present: { "claude-code": true, codex: false },
  };
  vi.mocked(api.connectors).mockResolvedValue({ connectors: [live] });
  render(<Connectors />);
  fireEvent.click(await screen.findByLabelText(/^Configure /));
  expect(screen.getByText(/Codex \(not installed here\)/)).toBeVisible();
});

it("detects new credentials without window blur, but never polls a hidden panel", async () => {
  const github = { ...row, name: "github", title: "GitHub", sources: ["gh-cli"] };
  vi.mocked(api.connectors).mockResolvedValueOnce({ connectors: [github] }).mockResolvedValue({ connectors: [{ ...github, credential: "gh-cli" }] });
  const visible = vi.spyOn(document, "visibilityState", "get").mockReturnValue("visible");
  vi.useFakeTimers();
  const view = render(<div hidden><Connectors /></div>);
  await act(async () => {});
  expect(screen.getByText("Not set up")).toBeInTheDocument();
  try {
    await act(async () => { await vi.advanceTimersByTimeAsync(60000); });
    expect(api.connectors).toHaveBeenCalledTimes(1);
    view.container.firstElementChild!.removeAttribute("hidden");
    await act(async () => { await vi.advanceTimersByTimeAsync(30000); });
    expect(screen.getByText("gh CLI login")).toBeVisible();
    await act(async () => { await vi.advanceTimersByTimeAsync(60000); });
    expect(api.connectors).toHaveBeenCalledTimes(2);
  } finally { vi.useRealTimers(); visible.mockRestore(); }
});

it("collapses a row to one control until it is configured", async () => {
  const live: Connector = { ...row, enabled: true, credential: "stored", stored_secret: true };
  vi.mocked(api.connectors).mockResolvedValue({ connectors: [live] });
  render(<Connectors />);
  expect(await screen.findByLabelText(/^Configure /)).toBeVisible();
  // Everything that used to crowd the row now waits behind one control.
  for (const hidden of ["Forget stored credentials", "Re-check", "Available to agents"]) {
    expect(screen.queryByText(hidden)).toBeNull();
  }
  fireEvent.click(screen.getByLabelText(/^Configure /));
  expect(screen.getByText("Forget stored credentials")).toBeVisible();
  expect(screen.getByLabelText("Porkbun available to agents")).toBeVisible();
});

it("names write access on the row, because it can destroy records", async () => {
  const live: Connector = { ...row, enabled: true, credential: "stored", stored_secret: true, write_access: true };
  vi.mocked(api.connectors).mockResolvedValue({ connectors: [live] });
  render(<Connectors />);
  expect(await screen.findByText("Can change DNS")).toBeVisible();
});

it("does not claim write access for a connector that is switched off", async () => {
  const off: Connector = { ...row, enabled: false, credential: "stored", stored_secret: true, write_access: true };
  vi.mocked(api.connectors).mockResolvedValue({ connectors: [off] });
  render(<Connectors />);
  expect(await screen.findByText("Off — not available to any agent")).toBeVisible();
  expect(screen.queryByText("Can change DNS")).toBeNull();
});

it("verifies each enabled connector once, not once per render", async () => {
  const live: Connector = { ...row, enabled: true, credential: "stored" };
  vi.mocked(api.connectors).mockResolvedValue({ connectors: [live] });
  render(<Connectors />);
  await screen.findByText(/3 tools/);
  fireEvent(window, new Event("focus"));
  fireEvent(window, new Event("focus"));
  await waitFor(() => expect(api.connectors).toHaveBeenCalled());
  // Each check spawns the real MCP server, so repeated status reads must not
  // launch it again.
  expect(api.verifyConnector).toHaveBeenCalledTimes(1);
});

it("leaves the panel open when availability is toggled inside it", async () => {
  const live: Connector = { ...row, enabled: true, credential: "stored", stored_secret: true };
  vi.mocked(api.connectors).mockResolvedValue({ connectors: [live] });
  vi.mocked(api.disableConnector).mockResolvedValue({ ...live, enabled: false });
  render(<Connectors />);
  fireEvent.click(await screen.findByLabelText(/^Configure /));
  fireEvent.click(screen.getByLabelText("Porkbun available to agents"));
  await waitFor(() => expect(api.disableConnector).toHaveBeenCalled());
  expect(screen.getByText("Forget stored credentials")).toBeVisible();
});

it("re-proves a connector after its credential or permissions change", async () => {
  const live: Connector = { ...row, enabled: true, credential: "stored", stored_secret: true };
  vi.mocked(api.connectors).mockResolvedValue({ connectors: [live] });
  vi.mocked(api.verifyConnector)
    .mockResolvedValueOnce({ name: "porkbun", ok: true, tools: 25, detail: null })
    .mockResolvedValue({ name: "porkbun", ok: true, tools: 12, detail: null });
  vi.mocked(api.enableConnector).mockResolvedValue({ ...live, write_access: false });
  render(<Connectors />);
  expect(await screen.findByText(/25 tools/)).toBeVisible();
  fireEvent.click(screen.getByLabelText(/^Configure /));
  fireEvent.click(screen.getByText("Save"));
  // A different permission can serve a different tool set, so the proved count
  // must not survive the change that invalidated it.
  expect(await screen.findByText(/12 tools/)).toBeVisible();
});
