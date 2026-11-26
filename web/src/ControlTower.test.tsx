import { waitFor, act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { api, PulseEvent, TowerInsights } from "./api";
import { defaultLayout } from "./Widgets";
import { oracleWidgets } from "./OracleWidgets";
import { ControlTower } from "./ControlTower";
import { TowerAgent } from "./tower";
import { edition, CODE, FULL, LITE } from "./test/editions";
import { StaticEdition } from "./edition";
vi.mock("./api", () => ({ authHeaders: () => ({}), api: { controlTower: vi.fn(), messageSession: vi.fn(), oracleChat: vi.fn(), fleetAsk: vi.fn(), clearOracleChat: vi.fn(), relay: vi.fn(), fleetPulse: vi.fn(), fleetMail: vi.fn() } }));
beforeEach(() => {
  vi.stubGlobal("fetch", vi.fn().mockImplementation(async (url: string) => new Response(JSON.stringify(url === "/tasks" ? { tasks: [] } : { revision: "default", instances: defaultLayout("oracle", oracleWidgets(() => {}, { backup: true })) }))));
  vi.mocked(api.oracleChat).mockResolvedValue({ messages: [] });
  vi.mocked(api.relay).mockResolvedValue({ notes: [{ id: "n1", session_key: "qa", name: "qa", folder: "Nourish", runtime: "claude-code", kind: "question", status: "open", created_at: NOW - 4 * 86_400_000, question: "Deploy now?" }], rules: [], open: 1 });
});
afterEach(() => { cleanup(); vi.useRealTimers(); vi.resetAllMocks(); vi.unstubAllGlobals(); });

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
function renderTower(features = [...FULL, "backup"]) {
  return render(<ControlTower onAnalytics={() => {}} agents={agents} now={NOW} onBack={() => {}} onOpenTerminal={() => {}} />, { wrapper: edition(features) });
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
  render(<ControlTower onAnalytics={() => {}} agents={agents} now={NOW} onBack={onBack} onOpenTerminal={() => {}} />, { wrapper: edition(FULL) });
  fireEvent.click(screen.getByRole("button", { name: "main-qa, Duckterm, Idle" }));
  fireEvent.keyDown(screen.getByLabelText("Message main-qa"), { key: "Escape" });
  expect(screen.queryByRole("dialog")).toBeNull();
  expect(onBack).not.toHaveBeenCalled();
  fireEvent.keyDown(screen.getByLabelText("Message Oracle"), { key: "Escape" });
  expect(onBack).not.toHaveBeenCalled();
  fireEvent.keyDown(window, { key: "Escape" });
  expect(onBack).toHaveBeenCalledTimes(1);
});

async function openNeeds() {
  fireEvent.click(await screen.findByRole("button", { name: "Show everything that needs you" }));
  return screen.getByRole("region", { name: "Needs you" });
}

it("opens every note that needs you under Your agents, answerable there, from the tile", async () => {
  vi.mocked(api.controlTower).mockResolvedValue(insights);
  render(<ControlTower onAnalytics={() => {}} agents={agents} now={NOW} onBack={() => {}} onOpenTerminal={() => {}} />, { wrapper: edition(FULL) });
  await screen.findByRole("button", { name: "Show everything that needs you" });
  expect(screen.queryByRole("region", { name: "Needs you" })).toBeNull();
  const needs = await openNeeds();
  expect(needs.compareDocumentPosition(screen.getByRole("region", { name: "Teams" }))).toBe(Node.DOCUMENT_POSITION_PRECEDING);
  expect(within(needs).getByText("Deploy now?")).toBeVisible();
  expect(within(needs).getByRole("textbox", { name: "Reply to qa" })).toBeVisible();
  fireEvent.click(within(needs).getByRole("button", { name: "Close" }));
  expect(screen.queryByRole("region", { name: "Needs you" })).toBeNull();
});

it("sends a menu question straight to the agent's terminal", async () => {
  vi.mocked(api.controlTower).mockResolvedValue(insights);
  vi.mocked(api.relay).mockResolvedValue({
    notes: [{ id: "c1", session_key: "rel", name: "release-dev", folder: "Duckterm", runtime: "claude-code", kind: "choice", status: "open", created_at: NOW - 60_000, questions: [{ question: "Merge PR #66?", options: ["Yes", "Hold"] }, { question: "Which rule applies?", options: ["release-dev releases"] }] }],
    rules: [],
    open: 1,
  });
  const onOpenTerminal = vi.fn();
  render(<ControlTower onAnalytics={() => {}} agents={agents} now={NOW} onBack={() => {}} onOpenTerminal={onOpenTerminal} />, { wrapper: edition(FULL) });
  const needs = await openNeeds();
  expect(needs).toHaveTextContent("Merge PR #66?");
  expect(needs).toHaveTextContent("Answer its menu there.");
  fireEvent.click(within(needs).getByRole("button", { name: "Open release-dev's terminal" }));
  expect(onOpenTerminal).toHaveBeenCalledWith("rel");
});


it("shows the remote cloud in Oracle fleet and needs-you ducks without adding nested tab stops", async () => {
  vi.mocked(api.controlTower).mockResolvedValue(insights);
  const remote = agent({ key: "qa", host: "build", hostLabel: "Build server", shownState: "waiting", hostOffline: true });
  render(<ControlTower onAnalytics={() => {}} agents={[...agents.filter(a => a.key !== "qa"), remote]} now={NOW} onBack={() => {}} onOpenTerminal={() => {}} />, { wrapper: edition(FULL) });
  const duck = screen.getByRole("button", { name: /qa, Duckterm, Waiting on you, Remote · Build server · Disconnected/ });
  expect(duck.querySelector('.rd-location-cloud')).not.toBeNull();
  expect(duck.querySelector('[tabindex]')).toBeNull();
  expect(screen.getByRole("button", { name: "architect, Duckterm, Busy" }).querySelector('.rd-location-cloud')).toBeNull();
  const needs = await openNeeds();
  expect(within(needs).getByRole("group", { name: "Remote · Build server · Disconnected" })).toBeInTheDocument();
});

it.each([["lite", LITE], ["code", CODE]])("%s shows factual widgets without hidden Oracle polls or saved Oracle widgets", async (_id, features) => {
  vi.useFakeTimers();
  vi.mocked(api.controlTower).mockResolvedValue(insights);
  renderTower(features as string[]);
  await act(async () => { await vi.advanceTimersByTimeAsync(12000); });
  expect(api.controlTower).toHaveBeenCalled();
  expect(screen.getByText("66 answered")).toBeVisible();
  expect(screen.getByText("1k")).toBeVisible();
  expect(screen.queryByLabelText("Message Oracle")).toBeNull();
  expect(screen.queryByRole("region", { name: "Needs you" })).toBeNull();
  expect(screen.queryByRole("article", { name: "Needs you" })).toBeNull();
  expect(screen.queryByRole("article", { name: "Remote sessions" })).toBeNull();
  expect(screen.queryByText(/Oracle nudge/)).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Add widget" }));
  expect(screen.getByText("Built-in fleet widgets")).toBeVisible();
  expect(screen.queryByRole("checkbox", { name: "Needs you" })).toBeNull();
  expect(screen.queryByRole("checkbox", { name: "Remote sessions" })).toBeNull();
  expect(api.relay).not.toHaveBeenCalled();
  expect(api.oracleChat).not.toHaveBeenCalled();
  expect(api.fleetAsk).not.toHaveBeenCalled();
});

it("drops cached Oracle notes and stops its polls when the capability is withdrawn", async () => {
  vi.useFakeTimers();
  vi.mocked(api.controlTower).mockResolvedValue(insights);
  const content = <ControlTower onAnalytics={() => {}} agents={agents} now={NOW} onBack={() => {}} onOpenTerminal={() => {}} />;
  const view = render(<StaticEdition features={FULL}>{content}</StaticEdition>);
  await act(async () => { await vi.advanceTimersByTimeAsync(0); });
  fireEvent.click(screen.getByRole("button", { name: "Show everything that needs you" }));
  expect(screen.getByRole("region", { name: "Needs you" })).toBeVisible();
  vi.mocked(api.relay).mockClear(); vi.mocked(api.oracleChat).mockClear();
  view.rerender(<StaticEdition features={LITE} id="lite">{content}</StaticEdition>);
  await act(async () => { await vi.advanceTimersByTimeAsync(12000); });
  expect(screen.queryByRole("region", { name: "Needs you" })).toBeNull();
  expect(screen.queryByText("Deploy now?")).toBeNull();
  expect(screen.queryByLabelText("Message Oracle")).toBeNull();
  expect(api.relay).not.toHaveBeenCalled();
  expect(api.oracleChat).not.toHaveBeenCalled();
});

it("starts no polling when its overview capability is unavailable", async () => {
  renderTower([]);
  await act(async () => {});
  expect(screen.queryByRole("heading", { name: /Control tower/ })).toBeNull();
  expect(api.controlTower).not.toHaveBeenCalled();
  expect(api.relay).not.toHaveBeenCalled();
  expect(api.oracleChat).not.toHaveBeenCalled();
  expect(fetch).not.toHaveBeenCalled();
});

function fleetMonitor(pulse: PulseEvent[] = [], fleet: TowerAgent[] = agents) {
  vi.mocked(api.controlTower).mockResolvedValue(insights);
  vi.mocked(api.fleetPulse).mockResolvedValue({ events: pulse });
  if (!vi.mocked(api.fleetMail).getMockImplementation()) vi.mocked(api.fleetMail).mockResolvedValue({ messages: [] });
  render(<ControlTower onAnalytics={() => {}} agents={fleet} now={NOW} onBack={() => {}} onOpenTerminal={() => {}} />, { wrapper: edition([...FULL, "fleet_monitor"]) });
}

it("with fleet_monitor, Agent fleet splits a team into pods by subfolder and says what each duck is doing", () => {
  fleetMonitor([], [
    agent({ key: "lead", group: "Duckterm", progress: { summary: "Shipped the Pulse. Next the pods.", deliverables: [], learnings: [], user_learnings: [], next_actions: [] } }),
    agent({ key: "dev", group: "Duckterm/core", state: "busy", shownState: "busy", lastTool: "Bash" }),
    agent({ key: "writer", group: "Duckterm/core", state: "busy", shownState: "busy", progress: { summary: "Rewriting the README intro. Then links.", deliverables: [], learnings: [], user_learnings: [], next_actions: [] } }),
    agent({ key: "qa", group: "Duckterm/qa", state: "waiting", shownState: "waiting" }),
  ]);
  fireEvent.click(screen.getByRole("tab", { name: "Agent fleet" }));
  const team = screen.getByRole("region", { name: "Teams" });
  const pods = within(team).getAllByRole("group").map((g) => [g.getAttribute("aria-label"), [...g.querySelectorAll("[data-duck]")].map((d) => d.getAttribute("data-duck"))]);
  expect(pods).toEqual([["Duckterm", ["lead"]], ["core", ["dev", "writer"]], ["qa", ["qa"]]]);
  const doing = (key: string) => team.querySelector(`[data-duck="${key}"] .rd-fleet-doing`)?.textContent;
  expect([doing("lead"), doing("dev"), doing("writer"), doing("qa")]).toEqual(["Idle", "Using Bash", "Rewriting the README intro.", "Waiting on you"]);
});

it("with fleet_monitor, a new message between agents draws one arrow, and only on Agent fleet", async () => {
  vi.mocked(api.fleetMail).mockResolvedValueOnce({ messages: [] }).mockResolvedValue({ messages: [{ id: "q1", sender: "main-qa", recipient: "architect", kind: "question", created_at: Date.now() }] });
  fleetMonitor();
  expect(api.fleetMail).not.toHaveBeenCalled();
  vi.useFakeTimers({ shouldAdvanceTime: true });
  fireEvent.click(screen.getByRole("tab", { name: "Agent fleet" }));
  await act(async () => { await Promise.resolve(); });
  expect(document.querySelectorAll(".rd-fleet-arrow")).toHaveLength(0);
  await act(async () => { await vi.advanceTimersByTimeAsync(5_000); });
  expect(document.querySelectorAll(".rd-fleet-arrow")).toHaveLength(1);
  // The same message on the next poll doesn't fly again, and the arrow clears.
  await act(async () => { await vi.advanceTimersByTimeAsync(5_000); });
  expect(document.querySelectorAll(".rd-fleet-arrow")).toHaveLength(0);
  fireEvent.click(screen.getByRole("tab", { name: "Ask Oracle" }));
  const calls = vi.mocked(api.fleetMail).mock.calls.length;
  await act(async () => { await vi.advanceTimersByTimeAsync(15_000); });
  expect(vi.mocked(api.fleetMail).mock.calls.length).toBe(calls);
});

it("with fleet_monitor, opens on Ask Oracle with tabs for the fleet and analytics", async () => {
  fleetMonitor();
  expect(screen.getByRole("heading", { name: /Fleet monitor/ })).toBeVisible();
  expect(screen.queryByText(/Control tower/)).toBeNull();
  expect(screen.getByRole("tab", { name: "Ask Oracle" })).toHaveAttribute("aria-selected", "true");
  expect(screen.getByLabelText("Message Oracle")).toBeVisible();
  expect(screen.queryByRole("region", { name: "Teams" })).toBeNull();
  fireEvent.click(screen.getByRole("tab", { name: "Agent fleet" }));
  expect(screen.getByRole("region", { name: "Teams" })).toBeVisible();
  expect(screen.getByLabelText("Message Oracle")).not.toBeVisible();
  fireEvent.click(screen.getByRole("tab", { name: "Analytics" }));
  expect(await screen.findByRole("button", { name: "Show everything that needs you" })).toBeVisible();
});

it("with fleet_monitor, an unsent Oracle draft survives a look at the other tabs", async () => {
  fleetMonitor();
  fireEvent.change(screen.getByLabelText("Message Oracle"), { target: { value: "Unsent owner draft" } });
  for (const other of ["Agent fleet", "Analytics"]) {
    fireEvent.click(screen.getByRole("tab", { name: other }));
    fireEvent.click(screen.getByRole("tab", { name: "Ask Oracle" }));
    expect(screen.getByLabelText("Message Oracle")).toHaveValue("Unsent owner draft");
  }
});

it("with fleet_monitor, arrow keys move between tabs and only the selected tab is a tab stop", () => {
  fleetMonitor();
  const chat = screen.getByRole("tab", { name: "Ask Oracle" });
  expect(screen.getAllByRole("tab").map((t) => t.tabIndex)).toEqual([0, -1, -1]);
  fireEvent.keyDown(chat, { key: "ArrowRight" });
  const fleet = screen.getByRole("tab", { name: "Agent fleet" });
  expect(fleet).toHaveAttribute("aria-selected", "true");
  expect(document.activeElement).toBe(fleet);
  fireEvent.keyDown(fleet, { key: "ArrowLeft" });
  fireEvent.keyDown(chat, { key: "ArrowLeft" });
  expect(screen.getByRole("tab", { name: "Analytics" })).toHaveAttribute("aria-selected", "true");
  expect(screen.getByRole("tabpanel", { name: "Analytics" })).toBeVisible();
});

it("with fleet_monitor, the banner and the tile open one Needs you window that Escape closes", async () => {
  fleetMonitor();
  const open = await screen.findByRole("button", { name: "Open Needs you" });
  expect(open.parentElement).toHaveTextContent("1 request from 1 agent. Oldest: qa, 4 days ago");
  open.focus();
  fireEvent.click(open);
  const win = screen.getByRole("dialog", { name: "Needs you" });
  expect(win).toHaveAttribute("aria-modal", "true");
  expect(document.activeElement).toBe(win);
  expect(within(win).getByText("Deploy now?")).toBeVisible();
  expect(within(win).getByRole("textbox", { name: "Reply to qa" })).toBeVisible();
  // Tab wraps inside the window instead of leaving it.
  const close = within(win).getByRole("button", { name: "Close" });
  const controls = win.querySelectorAll<HTMLElement>("button:not(:disabled),textarea:not(:disabled)");
  controls[controls.length - 1].focus();
  fireEvent.keyDown(controls[controls.length - 1], { key: "Tab" });
  expect(document.activeElement).toBe(close);
  fireEvent.keyDown(close, { key: "Tab", shiftKey: true });
  expect(document.activeElement).toBe(controls[controls.length - 1]);
  fireEvent.keyDown(window, { key: "Escape" });
  expect(screen.queryByRole("dialog", { name: "Needs you" })).toBeNull();
  expect(document.activeElement).toBe(open);
  fireEvent.click(screen.getByRole("tab", { name: "Analytics" }));
  const tile = await screen.findByRole("button", { name: "Show everything that needs you" });
  tile.focus();
  fireEvent.click(tile);
  expect(document.activeElement).toBe(screen.getByRole("dialog", { name: "Needs you" }));
  fireEvent.click(within(screen.getByRole("dialog", { name: "Needs you" })).getByRole("button", { name: "Close" }));
  expect(document.activeElement).toBe(tile);
});

it.each([
  [["qa", "qa", "qa"], "3 requests from 1 agent."],
  [["qa", "architect", "main-qa"], "3 requests from 3 agents."],
])("with fleet_monitor, the banner counts requests and the agents they come from (%j)", async (from, text) => {
  vi.mocked(api.relay).mockResolvedValue({ notes: from.map((key, i) => ({ id: `n${i}`, session_key: key, name: key, folder: "Duckterm", runtime: "claude-code", kind: "question", status: "open", created_at: NOW - (3 - i) * 60_000, question: `Q${i}?` })), rules: [], open: 3 });
  fleetMonitor();
  expect((await screen.findByRole("button", { name: "Open Needs you" })).parentElement).toHaveTextContent(text);
});

it("without fleet_monitor, the page is still the Control tower", async () => {
  vi.mocked(api.controlTower).mockResolvedValue(insights);
  render(<ControlTower onAnalytics={() => {}} agents={agents} now={NOW} onBack={() => {}} onOpenTerminal={() => {}} />, { wrapper: edition(FULL) });
  expect(screen.getByRole("heading", { name: /Control tower/ })).toBeVisible();
  expect(screen.queryByRole("tab")).toBeNull();
});


it("Pulse lists needs-you, merges, releases and artifacts newest first, and filters them", async () => {
  fleetMonitor([
    { kind: "pr", at: NOW - 60_000, title: "#470 Needs you counts match", name: "oracle-dev", session_key: "o", repo: "duckterm" },
    { kind: "release", at: NOW - 3_600_000, title: "v0.4.218", repo: "duckterm", detail: "4 merged PRs" },
    { kind: "artifact", at: NOW - 7_200_000, title: "Spec draft: Fleet monitor", name: "oracle-dev", session_key: "o" },
  ]);
  const pulse = screen.getByRole("complementary", { name: "Pulse" });
  await waitFor(() => expect(within(pulse).getAllByRole("listitem")).toHaveLength(4));
  const items = within(pulse).getAllByRole("listitem");
  expect(items.map((li) => li.querySelector(".rd-pulse-what")?.textContent)).toEqual([
    "oracle-dev merged a PR in duckterm",
    "duckterm released v0.4.218",
    "oracle-dev made an artifact",
    "qa needs you",
  ]);
  fireEvent.click(within(pulse).getByRole("button", { name: "PRs & releases" }));
  expect(within(pulse).getAllByRole("listitem")).toHaveLength(2);
  fireEvent.click(within(pulse).getByRole("button", { name: "Answer" }));
  expect(screen.getByRole("dialog", { name: "Needs you" })).toBeVisible();
});
