import { fireEvent, render, screen, waitFor, cleanup } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { Connectors } from "./Connectors";
import { api, Connector } from "./api";

vi.mock("./api", () => ({ api: { connectors: vi.fn(), enableConnector: vi.fn(), disableConnector: vi.fn(), forgetConnector: vi.fn() } }));
afterEach(() => { cleanup(); vi.resetAllMocks(); });

const row: Connector = { name: "porkbun", title: "Porkbun", description: "DNS", credential: null, identity: null, sources: ["stored"], write_access: false, enabled: false, installed: {}, ready: false, detail: null, managed: false, revoke_url: "https://porkbun.com/account/api" };

it("requires a separate write opt-in and sends read-only by default", async () => {
  vi.mocked(api.connectors).mockResolvedValue({ connectors: [row] });
  vi.mocked(api.enableConnector).mockResolvedValue({ ...row, enabled: true, credential: "stored" });
  render(<Connectors />);
  fireEvent.click(await screen.findByText("Connect"));
  expect(screen.getByRole("checkbox")).not.toBeChecked();
  fireEvent.change(screen.getByLabelText("Porkbun API key"), { target: { value: "test-key" } });
  fireEvent.change(screen.getByLabelText("Porkbun secret key"), { target: { value: "test-secret" } });
  fireEvent.click(screen.getByText("Verify and enable"));
  await waitFor(() => expect(api.enableConnector).toHaveBeenCalledWith("porkbun", "test-key", "test-secret", "stored", false));
  expect(await screen.findByText("Disable")).toBeVisible();
  expect(screen.queryByLabelText("Porkbun API key")).toBeNull();
});

it("disable preserves credentials and does not call forget", async () => {
  vi.mocked(api.connectors).mockResolvedValue({ connectors: [{ ...row, credential: "stored", enabled: true }] });
  vi.mocked(api.disableConnector).mockResolvedValue({ ...row, credential: "stored", enabled: false });
  render(<Connectors />);
  fireEvent.click(await screen.findByText("Disable"));
  await waitFor(() => expect(api.disableConnector).toHaveBeenCalledWith("porkbun"));
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
  await waitFor(() => expect(api.enableConnector).toHaveBeenCalledWith("huggingface", undefined, undefined, "anonymous", false));
  expect(screen.queryByDisplayValue("synthetic-private-token")).toBeNull();
});

it("retains refresh and personal Google setup instructions", async () => {
  const gmail: Connector = { ...row, name: "gmail", title: "Gmail", sources: ["google-oauth"] };
  vi.mocked(api.connectors).mockResolvedValueOnce({ connectors: [] }).mockResolvedValue({ connectors: [gmail] });
  render(<Connectors />);
  await screen.findByText("Connectors (0) · this computer");
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
  await waitFor(() => expect(api.enableConnector).toHaveBeenCalledWith("porkbun", undefined, undefined, "", false));
  expect(screen.queryByLabelText("Porkbun API key")).toBeNull();
});
