import { fireEvent, render, screen, waitFor, cleanup } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { Connectors } from "./Connectors";
import { api, Connector } from "./api";

vi.mock("./api", () => ({ api: { connectors: vi.fn(), enableConnector: vi.fn(), disableConnector: vi.fn(), forgetConnector: vi.fn(), verifyConnector: vi.fn() } }));
afterEach(() => { cleanup(); vi.resetAllMocks(); });

const row: Connector = { name: "porkbun", title: "Porkbun", description: "DNS", credential: null, identity: null, sources: ["stored"], write_access: false, enabled: false, installed: {}, ready: false, detail: null, managed: false, revoke_url: "https://porkbun.com/account/api", last_used: null, use_count: 0, harnesses: ["claude-code", "codex"], harnesses_present: { "claude-code": true, codex: true } };

it("requires a separate write opt-in and sends read-only by default", async () => {
  vi.mocked(api.connectors).mockResolvedValue({ connectors: [row] });
  vi.mocked(api.enableConnector).mockResolvedValue({ ...row, enabled: true, credential: "stored" });
  render(<Connectors />);
  fireEvent.click(await screen.findByText("Connect"));
  expect(screen.getByLabelText(/Allow changes to domains/)).not.toBeChecked();
  fireEvent.change(screen.getByLabelText("Porkbun API key"), { target: { value: "test-key" } });
  fireEvent.change(screen.getByLabelText("Porkbun secret key"), { target: { value: "test-secret" } });
  fireEvent.click(screen.getByText("Verify and enable"));
  await waitFor(() => expect(api.enableConnector).toHaveBeenCalledWith("porkbun", "test-key", "test-secret", "stored", false, "", ["claude-code", "codex"]));
  expect(await screen.findByText("Disable")).toBeVisible();
  expect(screen.queryByLabelText("Porkbun API key")).toBeNull();
});

it("disable preserves credentials and does not call forget", async () => {
  vi.mocked(api.connectors).mockResolvedValue({ connectors: [{ ...row, credential: "stored", enabled: true }] });
  vi.mocked(api.disableConnector).mockResolvedValue({ ...row, credential: "stored", enabled: false });
  render(<Connectors />);
  fireEvent.click(await screen.findByText("Disable"));
  await waitFor(() => expect(api.disableConnector).toHaveBeenCalledWith("porkbun", ""));
  expect(api.forgetConnector).not.toHaveBeenCalled();
  expect(await screen.findByText("Forget stored credentials")).toBeVisible();
});

it("does not expose secret administration on the hosted agent dashboard", async () => {
  vi.mocked(api.connectors).mockResolvedValue({ connectors: [{ ...row, managed: true }] });
  render(<Connectors />);
  expect(await screen.findByText("Managed through the remote connector administrator.")).toBeVisible();
  expect(screen.queryByText("Connect")).toBeNull();
});

it("keeps anonymous Hugging Face access available without sending another provider's token", async () => {
  const hf: Connector = { ...row, name: "huggingface", title: "Hugging Face", sources: ["anonymous", "stored"], ready: true };
  vi.mocked(api.connectors).mockResolvedValue({ connectors: [hf] });
  vi.mocked(api.enableConnector).mockResolvedValue({ ...hf, enabled: true, credential: "anonymous" });
  render(<Connectors />);
  fireEvent.click(await screen.findByText("Connect"));
  fireEvent.change(screen.getByLabelText("Hugging Face credential source"), { target: { value: "stored" } });
  fireEvent.change(screen.getByLabelText("Hugging Face API key"), { target: { value: "synthetic-private-token" } });
  fireEvent.change(screen.getByLabelText("Hugging Face credential source"), { target: { value: "anonymous" } });
  fireEvent.click(screen.getByText("Verify and enable"));
  await waitFor(() => expect(api.enableConnector).toHaveBeenCalledWith("huggingface", undefined, undefined, "anonymous", false, "", ["claude-code", "codex"]));
  expect(screen.queryByDisplayValue("synthetic-private-token")).toBeNull();
});

it("retains refresh and personal Google setup instructions", async () => {
  const gmail: Connector = { ...row, name: "gmail", title: "Gmail", sources: ["google-oauth"] };
  vi.mocked(api.connectors).mockResolvedValueOnce({ connectors: [] }).mockResolvedValue({ connectors: [gmail] });
  render(<Connectors />);
  await screen.findByText("Connectors (0) · This Mac");
  fireEvent(window, new Event("focus"));
  expect(await screen.findByText("Set up personal Gmail")).toBeVisible();
  expect(screen.getByText(/duckterm connector-auth gmail/)).toBeInTheDocument();
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
  expect(await screen.findByText(/Configured · not verified this session/)).toBeVisible();
  fireEvent.click(screen.getByText("Check now"));
  expect(await screen.findByText(/✓ Verified — 25 tools available/)).toBeVisible();
});

it("shows the server's own error when verification fails", async () => {
  const live: Connector = { ...row, enabled: true, credential: "stored", ready: true };
  vi.mocked(api.connectors).mockResolvedValue({ connectors: [live] });
  vi.mocked(api.verifyConnector).mockResolvedValue({
    name: "porkbun", ok: false, tools: 0, detail: "The connector did not start: uvx not found",
  });
  render(<Connectors />);
  fireEvent.click(await screen.findByText("Check now"));
  expect(await screen.findByText(/✗ The connector did not start: uvx not found/)).toBeVisible();
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
  fireEvent.click(await screen.findByText("Connect"));
  fireEvent.change(screen.getByLabelText("Porkbun API key"), { target: { value: "k" } });
  fireEvent.change(screen.getByLabelText("Porkbun secret key"), { target: { value: "s" } });
  fireEvent.click(screen.getByLabelText("Codex"));
  fireEvent.click(screen.getByText("Verify and enable"));
  await waitFor(() => expect(api.enableConnector).toHaveBeenCalledWith("porkbun", "k", "s", "stored", false, "", ["claude-code"]));
});

it("cannot submit a connector with no agent selected", async () => {
  vi.mocked(api.connectors).mockResolvedValue({ connectors: [row] });
  render(<Connectors />);
  fireEvent.click(await screen.findByText("Connect"));
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
  expect(await screen.findByText("Available to Claude Code · not Codex")).toBeVisible();
});

it("says an agent is missing rather than implying the connector serves it", async () => {
  const live: Connector = {
    ...row, enabled: true, credential: "stored",
    harnesses: ["claude-code", "codex"],
    harnesses_present: { "claude-code": true, codex: false },
  };
  vi.mocked(api.connectors).mockResolvedValue({ connectors: [live] });
  render(<Connectors />);
  expect(await screen.findByText(/Codex \(not installed here\)/)).toBeVisible();
});
