import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { api, PrRow, TowerInsights } from "./api";
import { Home } from "./Home";
import { homeDefaults } from "./homeGrid";
import { TowerAgent } from "./tower";
import { edition, CODE, FULL } from "./test/editions";
import { WidgetInstance } from "./Widgets";
vi.mock("./api", () => ({ authHeaders: () => ({}), api: { controlTower: vi.fn(), folderPrs: vi.fn(), oracleChat: vi.fn(), fleetAsk: vi.fn(), clearOracleChat: vi.fn(), relay: vi.fn() } }));
vi.mock("./OracleChat", () => ({ OracleChat: () => <p>Oracle chat</p> }));

const NOW = 10_000_000_000;
const insights: TowerInsights = { tokens: { days: 7, by_agent: {} }, mail: { sent: 0, answered: 0, nudges: 0 }, backup: { destination: null, status: null, finished_at: null }, remote: { available: false, count: 0 } };
const agent = (p: Partial<TowerAgent> & { key: string }) => ({ label: p.key, state: "idle", shownState: "idle", lastEventType: "", startedAt: 0, updatedAt: NOW, eventCount: 0, group: "Duckterm", ...p } as TowerAgent);
const agents = [agent({ key: "a", shownState: "waiting" }), agent({ key: "b", shownState: "busy", group: "Duckterm/core" }), agent({ key: "c", group: "Nourish" })];
const pr = (number: number, p: Partial<PrRow> = {}) => ({ repo: "/r", repo_name: "r", github: "o/r", branch: `b${number}`, base: "main", session: null, author: null, status: "open", label: "CI passing", needs_you: false, checks: null,
  pr: { number, title: `PR ${number}`, url: `https://github.com/o/r/pull/${number}`, state: "open" }, updated_at: number, ...p } as unknown as PrRow);

let saved: { instances: WidgetInstance[]; revision: string };
const puts: WidgetInstance[][] = [];
beforeEach(() => {
  saved = { instances: homeDefaults(), revision: "default" }; puts.length = 0;
  vi.stubGlobal("fetch", vi.fn(async (_path: string, init?: RequestInit) => {
    if (init?.method === "PUT") { saved = { ...JSON.parse(init.body as string), revision: `r${puts.length}` }; puts.push(saved.instances); }
    return new Response(JSON.stringify(saved));
  }));
  vi.mocked(api.controlTower).mockResolvedValue(insights);
  vi.mocked(api.relay).mockResolvedValue({ notes: [], rules: [], open: 0 });
  vi.mocked(api.oracleChat).mockResolvedValue({ messages: [] });
  vi.mocked(api.folderPrs).mockResolvedValue({ rows: [], repos: [] });
});
afterEach(() => { cleanup(); vi.resetAllMocks(); vi.unstubAllGlobals(); });

async function show(features = [...FULL, "home"], props: Partial<Parameters<typeof Home>[0]> = {}) {
  const Wrapper = edition(features);
  await act(async () => { render(<Wrapper><Home agents={agents} now={NOW} onTeams={() => undefined} onOpenTerminal={() => undefined} onAnalytics={() => undefined} {...props} /></Wrapper>); });
}

it("opens Teams from its tile and counts sessions by state across folders", async () => {
  const onTeams = vi.fn();
  await show(undefined, { onTeams });
  const teams = screen.getByRole("button", { name: /^Teams/ });
  expect(teams).toHaveTextContent("2 folders · 3 sessions");
  expect(teams).toHaveTextContent("1 need you");
  expect(teams).toHaveTextContent("1 working");
  fireEvent.click(teams);
  expect(onTeams).toHaveBeenCalledOnce();
  expect(within(screen.getByRole("article", { name: "Autopilot" })).getByText("Not available yet")).toBeVisible();
  expect(screen.getByText("Oracle chat")).toBeVisible();
});

it("leaves out the chat and pull requests when the edition lacks Oracle and PR review", async () => {
  await show([...CODE.filter(f => f !== "pr_review"), "home"]);
  expect(screen.queryByRole("article", { name: "Ask Oracle" })).toBeNull();
  expect(screen.queryByRole("article", { name: "Pull requests" })).toBeNull();
  expect(screen.getByRole("article", { name: "Teams" })).toBeVisible();
});

it("lists open pull requests once across top-level folders, needs-you first", async () => {
  vi.mocked(api.folderPrs).mockImplementation(async folder => ({ repos: [], rows: folder === "Duckterm"
    ? [pr(1), pr(2, { needs_you: true, label: "Needs your approval" }), pr(3, { status: "merged", pr: { number: 3, title: "PR 3", url: "", state: "merged" } as PrRow["pr"] })]
    : [pr(1), pr(4)] }));
  await show();
  expect(vi.mocked(api.folderPrs).mock.calls.map(call => call[0])).toEqual(["Duckterm", "Nourish"]);
  const rows = within(screen.getByRole("article", { name: "Pull requests" })).getAllByRole("listitem");
  expect(rows.map(row => row.querySelector("a")?.textContent)).toEqual(["PR 2", "PR 4", "PR 1"]);
  expect(rows[0]).toHaveTextContent("Needs your approval");
});

it("says why there are no pull requests when GitHub is not connected", async () => {
  vi.mocked(api.folderPrs).mockResolvedValue({ rows: [pr(5, { pr: null, status: "pushed" })], repos: [{ name: "duckterm", error: "Enable the GitHub connector to see pull request status" }] } as never);
  await show();
  expect(within(screen.getByRole("article", { name: "Pull requests" })).getByText("duckterm: Enable the GitHub connector to see pull request status")).toBeVisible();
});

it("moves a widget with the keyboard, pushes its neighbour down, and saves positions in reading order", async () => {
  await show();
  fireEvent.click(screen.getByRole("button", { name: "Edit layout" }));
  await act(async () => { fireEvent.keyDown(screen.getByRole("button", { name: "Move Teams" }), { key: "ArrowDown" }); });
  const box = (type: string) => puts.at(-1)!.find(item => item.type === type)!.params;
  expect(box("teams")).toEqual({ x: 0, y: 1, w: 3, h: 2 });
  expect(box("autopilot")).toEqual({ x: 0, y: 3, w: 3, h: 2 });
  expect(puts.at(-1)!.map(item => item.position)).toEqual(puts.at(-1)!.map((_, i) => i));
  await act(async () => { fireEvent.keyDown(screen.getByRole("button", { name: "Move Teams" }), { key: "ArrowRight", shiftKey: true }); });
  expect(box("teams")).toEqual({ x: 0, y: 1, w: 4, h: 2 });
});

it("removes and adds widgets, and the layout request carries the latest revision", async () => {
  await show();
  fireEvent.click(screen.getByRole("button", { name: "Edit layout" }));
  await act(async () => { fireEvent.click(screen.getByRole("button", { name: "Remove Usage · 7 days" })); });
  expect(saved.instances.map(item => item.type)).not.toContain("usage");
  fireEvent.click(screen.getByRole("button", { name: "Add widget" }));
  await act(async () => { fireEvent.click(screen.getByRole("button", { name: "Agent mail · 24h" })); });
  const put = vi.mocked(fetch).mock.calls.filter(call => call[1]?.method === "PUT").at(-1)!;
  expect(JSON.parse(put[1]!.body as string).revision).toBe("r0");
  expect(saved.instances.find(item => item.type === "mail")!.params).toEqual({ x: 0, y: 8, w: 3, h: 2 });
});
