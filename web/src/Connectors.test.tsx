import { act, fireEvent, render, screen, waitFor, cleanup } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { Connectors } from "./Connectors";
import { api, Connector } from "./api";

vi.mock("./api", () => ({ api: { connectors: vi.fn(), enableConnector: vi.fn(), disableConnector: vi.fn(), forgetConnector: vi.fn(), verifyConnector: vi.fn() } }));
afterEach(() => { cleanup(); vi.resetAllMocks(); });

const row: Connector = {
  name: "github", title: "GitHub", description: "Repos, PRs, issues, and actions",
  credential: null, identity: null, sources: ["gh-cli", "stored"],
  enabled: false, duckterm_uses: false, agents_use: false,
  agents_off_by_choice: true, unreachable_harnesses: [],
  installed: {}, runner_ready: true, ready: false, detail: null, managed: false,
  revoke_url: "https://github.com/settings/tokens", last_used: null, use_count: 0,
  harnesses: ["claude-code", "codex"], harnesses_present: { "claude-code": true, codex: true },
};

// Connected, both doors open.
const live: Connector = {
  ...row, credential: "gh-cli", identity: "utsavanand",
  enabled: true, duckterm_uses: true, agents_use: true,
  agents_off_by_choice: false, ready: true,
};

it("says Duckterm still uses the credential when agents are turned off", async () => {
  const off: Connector = { ...live, enabled: false, agents_use: false, agents_off_by_choice: true };
  vi.mocked(api.connectors).mockResolvedValue({ connectors: [off] });
  render(<Connectors />);
  // The bug this view exists to fix: one "disabled" line for two independent
  // facts read as Duckterm having dropped the credential too.
  expect(await screen.findByText("Duckterm uses it")).toBeVisible();
  expect(screen.getByText("Agents cannot use it")).toBeVisible();
});

it("distinguishes a lost harness entry from the owner turning agents off", async () => {
  const lost: Connector = {
    ...live, enabled: false, agents_use: false,
    agents_off_by_choice: false, unreachable_harnesses: ["codex"],
  };
  vi.mocked(api.connectors).mockResolvedValue({ connectors: [lost] });
  render(<Connectors />);
  // Same `enabled: false`, different cause and different fix, so the panel
  // must not describe both with the Disable wording.
  expect(await screen.findByText(/Codex has no entry for it/)).toBeVisible();
  expect(screen.queryByText(/Your login stays saved/)).toBeNull();
});

it("connects straight from the CLI login without asking for a paste", async () => {
  vi.mocked(api.connectors).mockResolvedValue({ connectors: [row] });
  vi.mocked(api.enableConnector).mockResolvedValue(live);
  render(<Connectors />);
  fireEvent.click(await screen.findByText("Connect"));
  await waitFor(() => expect(api.enableConnector).toHaveBeenCalledWith("github", undefined, "gh-cli", "", ["claude-code", "codex"]));
  expect(await screen.findByText(/^Agents use it/)).toBeVisible();
});

it("warns that agents get nothing when no MCP server can run here", async () => {
  const noRunner: Connector = { ...row, runner_ready: false, detail: "install github-mcp-server or Docker so agents can run it" };
  vi.mocked(api.connectors).mockResolvedValue({ connectors: [noRunner] });
  render(<Connectors />);
  // Previously a detail string beside a row that still offered Connect, so the
  // owner could connect successfully and find agents had no GitHub.
  expect(await screen.findByText(/Before connecting: install github-mcp-server or Docker/)).toBeVisible();
});

it("accepts a pasted token and says a fine-grained one can be narrower", async () => {
  vi.mocked(api.connectors).mockResolvedValue({ connectors: [row] });
  vi.mocked(api.enableConnector).mockResolvedValue({ ...live, credential: "stored" });
  render(<Connectors />);
  fireEvent.click(await screen.findByText("Paste a token"));
  // gh cannot drop below repo + read:org + gist, so this is the only way to
  // hand agents less than full repo write.
  expect(screen.getByText(/A fine-grained token can be read-only/)).toBeVisible();
  fireEvent.change(screen.getByLabelText("GitHub personal access token"), { target: { value: "github_pat_x" } });
  fireEvent.click(screen.getByText("Verify and share"));
  await waitFor(() => expect(api.enableConnector).toHaveBeenCalledWith("github", "github_pat_x", "stored", "", ["claude-code", "codex"]));
});

it("withdraws from agents without deleting the credential", async () => {
  vi.mocked(api.connectors).mockResolvedValue({ connectors: [live] });
  vi.mocked(api.disableConnector).mockResolvedValue({ ...live, enabled: false, agents_use: false, agents_off_by_choice: true });
  render(<Connectors />);
  fireEvent.click(await screen.findByText("Stop sharing with agents"));
  await waitFor(() => expect(api.disableConnector).toHaveBeenCalledWith("github", ""));
  expect(api.forgetConnector).not.toHaveBeenCalled();
  expect(await screen.findByText("Share with agents")).toBeVisible();
  expect(screen.getByText("Duckterm uses it")).toBeVisible();
});

it("does not expose secret administration on the hosted agent dashboard", async () => {
  vi.mocked(api.connectors).mockResolvedValue({ connectors: [{ ...row, managed: true }] });
  render(<Connectors />);
  expect(await screen.findByText("Managed through the remote connector administrator.")).toBeVisible();
  expect(screen.queryByText("Connect")).toBeNull();
});

it("picks up a connector that appears while the window regains focus", async () => {
  vi.mocked(api.connectors).mockResolvedValueOnce({ connectors: [] }).mockResolvedValue({ connectors: [row] });
  render(<Connectors />);
  await screen.findByText("Connectors · This Mac");
  fireEvent(window, new Event("focus"));
  expect(await screen.findByText("GitHub")).toBeVisible();
  fireEvent.click(screen.getByText("Refresh"));
  await waitFor(() => expect(api.connectors).toHaveBeenCalledTimes(3));
});

it("claims verification only after a check, and reports the tool count it proved", async () => {
  vi.mocked(api.connectors).mockResolvedValue({ connectors: [live] });
  vi.mocked(api.verifyConnector).mockResolvedValue({ name: "github", ok: true, tools: 25, detail: null });
  render(<Connectors />);
  expect(await screen.findByText(/Not verified/)).toBeVisible();
  fireEvent.click(screen.getByText("Check now"));
  expect(await screen.findByText(/✓ 25 tools/)).toBeVisible();
});

it("shows the server's own error when verification fails", async () => {
  vi.mocked(api.connectors).mockResolvedValue({ connectors: [live] });
  vi.mocked(api.verifyConnector).mockResolvedValue({
    name: "github", ok: false, tools: 0, detail: "The connector did not start: github-mcp-server not found",
  });
  render(<Connectors />);
  fireEvent.click(await screen.findByText("Check now"));
  expect(await screen.findByText(/✗ The connector did not start: github-mcp-server not found/)).toBeVisible();
});

it("says use is unrecorded rather than claiming a connector is unused", async () => {
  vi.mocked(api.connectors).mockResolvedValue({ connectors: [live] });
  render(<Connectors />);
  expect(await screen.findByText(/No recorded use/)).toBeVisible();
  cleanup();
  vi.mocked(api.connectors).mockResolvedValue({
    connectors: [{ ...live, last_used: Date.now() - 2 * 3600 * 1000, use_count: 90 }],
  });
  render(<Connectors />);
  expect(await screen.findByText(/Used 2h ago · 90 calls/)).toBeVisible();
});

it("sends only the agents left checked, and refuses to send none", async () => {
  vi.mocked(api.connectors).mockResolvedValue({ connectors: [row] });
  vi.mocked(api.enableConnector).mockResolvedValue({ ...live, harnesses: ["claude-code"] });
  render(<Connectors />);
  fireEvent.click(await screen.findByText("Paste a token"));
  fireEvent.change(screen.getByLabelText("GitHub personal access token"), { target: { value: "k" } });
  fireEvent.click(screen.getByLabelText("Codex"));
  fireEvent.click(screen.getByText("Verify and share"));
  await waitFor(() => expect(api.enableConnector).toHaveBeenCalledWith("github", "k", "stored", "", ["claude-code"]));
});

it("cannot submit with no agent selected", async () => {
  vi.mocked(api.connectors).mockResolvedValue({ connectors: [row] });
  render(<Connectors />);
  fireEvent.click(await screen.findByText("Paste a token"));
  fireEvent.change(screen.getByLabelText("GitHub personal access token"), { target: { value: "k" } });
  fireEvent.click(screen.getByLabelText("Codex"));
  fireEvent.click(screen.getByLabelText("Claude Code"));
  expect(screen.getByText("Verify")).toBeDisabled();
  expect(api.enableConnector).not.toHaveBeenCalled();
});

it("names the agents a connector reaches instead of reporting a config entry", async () => {
  vi.mocked(api.connectors).mockResolvedValue({ connectors: [{ ...live, harnesses: ["claude-code"] }] });
  render(<Connectors />);
  expect(await screen.findByText(/Agents use it — Claude Code/)).toBeVisible();
});

it("says an agent is missing rather than implying the connector serves it", async () => {
  vi.mocked(api.connectors).mockResolvedValue({
    connectors: [{ ...live, harnesses_present: { "claude-code": true, codex: false } }],
  });
  render(<Connectors />);
  expect(await screen.findByText(/Codex \(not installed here\)/)).toBeVisible();
});

it("detects new credentials without window blur, but never polls a hidden panel", async () => {
  vi.mocked(api.connectors).mockResolvedValueOnce({ connectors: [row] }).mockResolvedValue({ connectors: [live] });
  const visible = vi.spyOn(document, "visibilityState", "get").mockReturnValue("visible");
  vi.useFakeTimers();
  const view = render(<div hidden><Connectors /></div>);
  await act(async () => {});
  expect(screen.getByText("Not connected")).toBeInTheDocument();
  try {
    await act(async () => { await vi.advanceTimersByTimeAsync(60000); });
    expect(api.connectors).toHaveBeenCalledTimes(1);
    view.container.firstElementChild!.removeAttribute("hidden");
    await act(async () => { await vi.advanceTimersByTimeAsync(30000); });
    expect(screen.getByText("utsavanand")).toBeVisible();
    await act(async () => { await vi.advanceTimersByTimeAsync(60000); });
    expect(api.connectors).toHaveBeenCalledTimes(2);
  } finally { vi.useRealTimers(); visible.mockRestore(); }
});

it("sends the revoke link to the page the server chose, in a new tab", async () => {
  // The owner approved this link as a phrase rather than the standalone button
  // it used to be, so nothing asserted it existed. It is the only route out of
  // the panel to GitHub, and its href is chosen server-side per credential
  // source (a CLI login and a pasted token live on different pages), so the
  // component must pass revoke_url through rather than hardcode a page.
  vi.mocked(api.connectors).mockResolvedValue({
    connectors: [{ ...live, revoke_url: "https://github.com/settings/applications" }],
  });
  render(<Connectors />);
  const link = await screen.findByRole("link", { name: /on github\.com/ });
  expect(link).toHaveAttribute("href", "https://github.com/settings/applications");
  // A new tab, and no referrer leaked to github.com.
  expect(link).toHaveAttribute("target", "_blank");
  expect(link).toHaveAttribute("rel", "noreferrer");
  // Placement: inside the connected block, beside Sign out.
  expect(link.closest(".rd-connector-installed")).not.toBeNull();
});

it("does not offer a revoke link before there is anything to revoke", async () => {
  vi.mocked(api.connectors).mockResolvedValue({ connectors: [row] });
  render(<Connectors />);
  await screen.findByText("Not connected");
  expect(screen.queryByRole("link", { name: /github\.com/ })).toBeNull();
});
