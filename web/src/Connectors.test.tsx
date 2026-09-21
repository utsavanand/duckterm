import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { Connectors } from "./Connectors";
import { api, Connector } from "./api";

vi.mock("./api", () => ({ api: {
  connectors: vi.fn(), enableConnector: vi.fn(), disableConnector: vi.fn(),
} }));
vi.mock("./ui", () => ({ useToast: () => vi.fn() }));
const hf: Connector = {
  name: "huggingface", title: "Hugging Face", description: "Discover models and datasets",
  enabled: false, ready: true, credential: null, detail: "Public discovery; token optional",
  installed: { codex: false, "claude-code": false },
};
afterEach(() => { cleanup(); vi.resetAllMocks(); });

it("connects without a token and can disconnect", async () => {
  vi.mocked(api.connectors).mockResolvedValue({ connectors: [hf] });
  vi.mocked(api.enableConnector).mockResolvedValue({ ...hf, enabled: true });
  vi.mocked(api.disableConnector).mockResolvedValue(hf);
  render(<Connectors />);
  fireEvent.click(await screen.findByRole("button", { name: "Connect" }));
  expect(api.enableConnector).toHaveBeenCalledWith("huggingface", undefined, undefined);
  fireEvent.click(await screen.findByRole("button", { name: "Disconnect" }));
  await waitFor(() => expect(api.disableConnector).toHaveBeenCalledWith("huggingface"));
  expect(await screen.findByRole("button", { name: "Connect" })).toBeEnabled();
});

it("accepts an optional token and clears it after connecting", async () => {
  vi.mocked(api.connectors).mockResolvedValue({ connectors: [hf] });
  vi.mocked(api.enableConnector).mockResolvedValue({ ...hf, enabled: true, credential: "stored" });
  render(<Connectors />);
  fireEvent.click(await screen.findByRole("button", { name: "Add optional token" }));
  const input = screen.getByPlaceholderText("Hugging Face token (hf_…)");
  expect(input).toHaveAttribute("type", "password");
  fireEvent.change(input, { target: { value: "hf_example" } });
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  await screen.findByRole("button", { name: "Disconnect" });
  expect(api.enableConnector).toHaveBeenCalledWith("huggingface", "hf_example", undefined);
  expect(screen.queryByDisplayValue("hf_example")).toBeNull();
});

it("does not send another connector's draft credential to Hugging Face", async () => {
  const github = { ...hf, name: "github", title: "GitHub", ready: false };
  vi.mocked(api.connectors).mockResolvedValue({ connectors: [github, hf] });
  vi.mocked(api.enableConnector).mockResolvedValue({ ...hf, enabled: true });
  render(<Connectors />);
  const githubRow = (await screen.findByText("GitHub")).closest(".rd-connector") as HTMLElement;
  fireEvent.click(within(githubRow).getByRole("button", { name: "Connect" }));
  fireEvent.change(screen.getByPlaceholderText("GitHub personal access token"), {
    target: { value: "ghp_private" },
  });
  const hfRow = screen.getByText("Hugging Face").closest(".rd-connector") as HTMLElement;
  fireEvent.click(within(hfRow).getByRole("button", { name: "Connect" }));
  await waitFor(() => expect(api.enableConnector).toHaveBeenCalledWith("huggingface", undefined, undefined));
});

it("shows the runtime requirement when Node.js is unavailable", async () => {
  vi.mocked(api.connectors).mockResolvedValue({ connectors: [
    { ...hf, ready: false, detail: "install Node.js 22+ with npm to connect Hugging Face" },
  ] });
  render(<Connectors />);
  expect(await screen.findByRole("button", { name: "Connect" })).toBeDisabled();
  expect(screen.getByText(/install Node.js/)).toBeVisible();
});

it("refreshes the connector catalog on focus and on request", async () => {
  const github = { ...hf, name: "github", title: "GitHub" };
  vi.mocked(api.connectors).mockResolvedValueOnce({ connectors: [github] });
  vi.mocked(api.connectors).mockResolvedValue({ connectors: [github, hf] });
  render(<Connectors />);
  await screen.findByText("Connectors (1)");
  expect(screen.queryByText("Hugging Face")).toBeNull();
  fireEvent(window, new Event("focus"));
  expect(await screen.findByText("Hugging Face")).toBeVisible();
  expect(screen.getByText("Connectors (2)")).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "Refresh" }));
  await waitFor(() => expect(api.connectors).toHaveBeenCalledTimes(3));
});

it("shows personal Gmail setup without asking for a password", async () => {
  vi.mocked(api.connectors).mockResolvedValue({ connectors: [{ ...hf,
    name: "gmail", title: "Gmail", ready: false, description: "Search and read personal Gmail",
  }] });
  render(<Connectors />);
  expect(await screen.findByText("Set up personal Gmail")).toBeVisible();
  expect(screen.getByRole("button", { name: "Connect" })).toBeDisabled();
  expect(screen.getByText(/duckterm connector-auth gmail/)).toBeInTheDocument();
  expect(screen.queryByRole("textbox")).toBeNull();
});

it("enables a shared connector without requesting local credentials", async () => {
  const shared = { ...hf, name: "github", title: "GitHub", managed: true, ready: true };
  vi.mocked(api.connectors).mockResolvedValue({ connectors: [shared] });
  vi.mocked(api.enableConnector).mockResolvedValue({ ...shared, enabled: true });
  render(<Connectors />);
  fireEvent.click(await screen.findByRole("button", { name: "Connect" }));
  await screen.findByRole("button", { name: "Disconnect" });
  expect(api.enableConnector).toHaveBeenCalledWith("github", undefined, undefined);
  expect(screen.queryByPlaceholderText("GitHub personal access token")).toBeNull();
});
