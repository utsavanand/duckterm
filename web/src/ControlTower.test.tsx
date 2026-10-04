import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { api, TowerInsights } from "./api";
import { defaultLayout } from "./Widgets";
import { oracleWidgets } from "./OracleWidgets";
import { ControlTower } from "./ControlTower";
import { TowerAgent } from "./tower";
vi.mock("./api", () => ({ authHeaders: () => ({}), api: { controlTower: vi.fn(), messageSession: vi.fn(), oracleChat: vi.fn(), fleetAsk: vi.fn(), clearOracleChat: vi.fn(), relay: vi.fn() } }));
beforeEach(() => {
  vi.stubGlobal("fetch", vi.fn().mockImplementation(async (url: string) => new Response(JSON.stringify(url === "/tasks" ? { tasks: [] } : { revision: "default", instances: defaultLayout("oracle", oracleWidgets(() => {})) }))));
  vi.mocked(api.oracleChat).mockResolvedValue({ messages: [] });
  vi.mocked(api.relay).mockResolvedValue({ notes: [{ id: "n1", session_key: "qa", name: "qa", folder: "Nourish", runtime: "claude-code", kind: "question", status: "open", created_at: NOW - 4 * 86_400_000, question: "Deploy now?" }], rules: [], open: 1 });
});
afterEach(() => { cleanup(); vi.resetAllMocks(); vi.unstubAllGlobals(); });

const NOW = 10_000_000_000;
const insights: TowerInsights = {
  tokens: { days: 7, by_agent: { "claude-code": { input: 0, cache_read: 960, cache_write: 30, output: 10 }, codex: { input: 20, cache_read: 0, cache_write: 0, output: 0 } } },
  mail: { sent: 74, answered: 66, nudges: 18 },
  backup: { destination: "gcs", status: null, finished_at: null },
  remote: { available: false, count: 0 },
};
function agent(p: Partial<TowerAgent> & { key: string }): TowerAgent {
  return { label: p.key, state: "idle", shownState: "idle", lastEventType: "", startedAt: 0, updatedAt: NOW, eventCount: 0, group: "Duckterm", ...p } as TowerAgent;
}
const agents = [
  agent({ key: "main-qa", inboxPending: 2, runtime: "codex", progress: { summary: "Fixed the fork race. Next it will ship.", deliverables: [], learnings: [], user_learnings: [], next_actions: [] } }),
  agent({ key: "architect", state: "busy", shownState: "busy", lastTool: "Bash" }),
  agent({ key: "qa", group: "Nourish", state: "waiting", shownState: "waiting", updatedAt: NOW - 4 * 86_400_000 }),
];
function renderTower() {
  return render(<ControlTower onAnalytics={() => {}} agents={agents} now={NOW} onBack={() => {}} onOpenTerminal={() => {}} />);
}

it("shows fleet tiles with the cache share and a missing backup as a warning", async () => {
  vi.mocked(api.controlTower).mockResolvedValue(insights);
  renderTower();
  expect(await screen.findByText("1k")).toBeVisible();
  expect(screen.getByText(/95% read from cache/)).toBeVisible();
  expect(screen.getByText(/Destination set \(Google Cloud Storage\), no completed backup/)).toBeVisible();
  expect(await screen.findByText("Oldest: qa (Nourish), 4 days ago")).toBeVisible();
});

it("shows what an agent is working on when hovered, and sends a pinned message to its inbox", async () => {
  vi.mocked(api.controlTower).mockResolvedValue(insights);
  vi.mocked(api.messageSession).mockResolvedValue({ delivered: "inbox" });
  renderTower();
  const duck = screen.getByRole("button", { name: "main-qa, Duckterm, Idle" });
  fireEvent.mouseEnter(duck);
  expect(screen.getByRole("tooltip")).toHaveTextContent("Fixed the fork race.");
  expect(screen.getByRole("tooltip")).not.toHaveTextContent("Next it will ship.");
  fireEvent.click(duck);
  const box = screen.getByLabelText("Message main-qa");
  fireEvent.change(box, { target: { value: "please rerun QA" } });
  await act(async () => { fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Send" })); });
  expect(api.messageSession).toHaveBeenCalledWith("main-qa", "please rerun QA", "inbox");
  expect(within(screen.getByRole("dialog")).getByRole("status")).toHaveTextContent("In main-qa's inbox.");
});

it("offers typing into the prompt only for idle agents and shows the server's refusal", async () => {
  vi.mocked(api.controlTower).mockResolvedValue(insights);
  vi.mocked(api.messageSession).mockRejectedValue(new Error("Its prompt isn't empty, so there may be a draft. Send it to the inbox instead."));
  renderTower();
  fireEvent.click(screen.getByRole("button", { name: "architect, Duckterm, Busy" }));
  expect(screen.getByRole("button", { name: "Type into its prompt" })).toBeDisabled();
  fireEvent.keyDown(window, { key: "Escape" });
  fireEvent.click(screen.getByRole("button", { name: "main-qa, Duckterm, Idle" }));
  fireEvent.click(screen.getByRole("button", { name: "Type into its prompt" }));
  fireEvent.change(screen.getByLabelText("Message main-qa"), { target: { value: "go" } });
  await act(async () => { fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Send" })); });
  expect(api.messageSession).toHaveBeenCalledWith("main-qa", "go", "prompt");
  expect(screen.getByRole("alert")).toHaveTextContent("there may be a draft");
});

it("Escape closes a pinned card first, then returns to the sessions, but not while typing", async () => {
  vi.mocked(api.controlTower).mockResolvedValue(insights);
  const onBack = vi.fn();
  render(<ControlTower onAnalytics={() => {}} agents={agents} now={NOW} onBack={onBack} onOpenTerminal={() => {}} />);
  fireEvent.click(screen.getByRole("button", { name: "main-qa, Duckterm, Idle" }));
  fireEvent.keyDown(screen.getByLabelText("Message main-qa"), { key: "Escape" });
  expect(screen.queryByRole("dialog")).toBeNull();
  expect(onBack).not.toHaveBeenCalled();
  fireEvent.keyDown(screen.getByLabelText("Message Oracle"), { key: "Escape" });
  expect(onBack).not.toHaveBeenCalled();
  fireEvent.keyDown(window, { key: "Escape" });
  expect(onBack).toHaveBeenCalledTimes(1);
});

it("sends a menu question straight to the agent's terminal", async () => {
  vi.mocked(api.controlTower).mockResolvedValue(insights);
  vi.mocked(api.relay).mockResolvedValue({
    notes: [{ id: "c1", session_key: "rel", name: "release-dev", folder: "Duckterm", runtime: "claude-code", kind: "choice", status: "open", created_at: NOW - 60_000, questions: [{ question: "Merge PR #66?", options: ["Yes", "Hold"] }, { question: "Which rule applies?", options: ["release-dev releases"] }] }],
    rules: [],
    open: 1,
  });
  const onOpenTerminal = vi.fn();
  render(<ControlTower onAnalytics={() => {}} agents={agents} now={NOW} onBack={() => {}} onOpenTerminal={onOpenTerminal} />);
  const needs = await screen.findByRole("region", { name: "Needs you" });
  expect(needs).not.toHaveTextContent("Answer in the chat");
  const row = await within(needs).findByRole("button", { name: /release-dev/ });
  expect(row).toHaveTextContent("Merge PR #66? Answer in its terminal.");
  fireEvent.click(row);
  expect(onOpenTerminal).toHaveBeenCalledWith("rel");
});


it("shows the remote cloud in Oracle fleet and needs-you ducks without adding nested tab stops", async () => {
  vi.mocked(api.controlTower).mockResolvedValue(insights);
  const remote = agent({ key: "qa", host: "build", hostLabel: "Build server", shownState: "waiting", hostOffline: true });
  render(<ControlTower onAnalytics={() => {}} agents={[...agents.filter(a => a.key !== "qa"), remote]} now={NOW} onBack={() => {}} onOpenTerminal={() => {}} />);
  const duck = screen.getByRole("button", { name: /qa, Duckterm, Waiting on you, Remote · Build server · Disconnected/ });
  expect(duck.querySelector('.rd-location-cloud')).not.toBeNull();
  expect(duck.querySelector('[tabindex]')).toBeNull();
  expect(screen.getByRole("button", { name: "architect, Duckterm, Busy" }).querySelector('.rd-location-cloud')).toBeNull();
  const needs = await screen.findByRole("region", { name: "Needs you" });
  expect(within(needs).getByRole("group", { name: "Remote · Build server · Disconnected" })).toBeInTheDocument();
});
