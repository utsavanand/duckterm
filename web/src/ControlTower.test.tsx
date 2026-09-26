import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { api, TowerInsights } from "./api";
import { ControlTower } from "./ControlTower";
import { TowerAgent } from "./tower";
vi.mock("./api", () => ({ api: { controlTower: vi.fn(), messageSession: vi.fn(), oracleChat: vi.fn(), fleetAsk: vi.fn(), clearOracleChat: vi.fn() } }));
beforeEach(() => { vi.mocked(api.oracleChat).mockResolvedValue({ messages: [] }); });
afterEach(() => { cleanup(); vi.resetAllMocks(); });

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
  return render(<ControlTower agents={agents} now={NOW} onBack={() => {}} onOpenTerminal={() => {}} />);
}

it("shows fleet tiles with the cache share and a missing backup as a warning", async () => {
  vi.mocked(api.controlTower).mockResolvedValue(insights);
  renderTower();
  expect(await screen.findByText("1k")).toBeVisible();
  expect(screen.getByText(/95% read from cache/)).toBeVisible();
  expect(screen.getByText(/Destination set \(Google Cloud Storage\), no completed backup/)).toBeVisible();
  expect(screen.getByText("Oldest: qa (Nourish), waiting 4 days")).toBeVisible();
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
  expect(screen.getByRole("status")).toHaveTextContent("In main-qa's inbox.");
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
