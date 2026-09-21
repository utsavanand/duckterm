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
