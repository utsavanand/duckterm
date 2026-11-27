import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { api, PrRow, TowerInsights } from "./api";
import { Home, prRisk, safeFile } from "./Home";
import { homeDefaults } from "./homeGrid";
import { TowerAgent } from "./tower";
import { edition, CODE, FULL } from "./test/editions";
import { WidgetInstance } from "./Widgets";
vi.mock("./api", () => ({ authHeaders: () => ({}), api: { controlTower: vi.fn(), folderPrs: vi.fn(), oracleChat: vi.fn(), fleetAsk: vi.fn(), messageSession: vi.fn(), clearOracleChat: vi.fn(), relay: vi.fn(), relayAnswer: vi.fn(), reviewDiff: vi.fn(), approvalState: vi.fn(), approvalKey: vi.fn() } }));

const NOW = 10_000_000_000;
const insights: TowerInsights = { tokens: { days: 7, by_agent: {} }, mail: { sent: 0, answered: 0, nudges: 0 }, backup: { destination: null, status: null, finished_at: null }, remote: { available: false, count: 0 } };
const agent = (p: Partial<TowerAgent> & { key: string }) => ({ label: p.key, state: "idle", shownState: "idle", lastEventType: "", startedAt: 0, updatedAt: NOW, eventCount: 0, group: "Duckterm", ...p } as TowerAgent);
const agents = [agent({ key: "a", shownState: "waiting" }), agent({ key: "b", shownState: "busy", group: "Duckterm/core" }), agent({ key: "c", group: "Nourish" })];
const pr = (number: number, p: Partial<PrRow> = {}) => ({ repo: "/r", repo_name: "r", github: "o/r", branch: `b${number}`, base: "main", session: null, author: null, status: "open", label: "CI passing", needs_you: false, checks: null,
  pr: { number, title: `PR ${number}`, url: `https://github.com/o/r/pull/${number}`, state: "open" }, updated_at: number, ...p } as unknown as PrRow);

const row = (day: string, agent: string, output: number) => ({ day, agent, output, input: 0, cache_read: 0, cache_write: 0, model: null, session: "s", session_name: "s", folder: "", folder_name: "" });
const tokens = { version: 1, timezone: "UTC", days: 7, today: "2026-11-20", earliest_day: "2026-11-01", options: { agents: [], folders: [], sessions: [] },
  rows: [row("2026-11-14", "claude-code", 1000), row("2026-11-20", "claude-code", 2000), row("2026-11-20", "codex", 1000)] };
let saved: { instances: WidgetInstance[]; revision: string };
const puts: WidgetInstance[][] = [];
beforeEach(() => {
  saved = { instances: homeDefaults(), revision: "default" }; puts.length = 0;
  vi.stubGlobal("fetch", vi.fn(async (path: string, init?: RequestInit) => {
    if (path.startsWith("/analytics/tokens")) return new Response(JSON.stringify(tokens));
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
  const teams = within(screen.getByRole("article", { name: "Teams" })).getByRole("button");
  expect(teams).toHaveTextContent("2 folders · 3 sessions");
  expect(teams).toHaveTextContent("1 need you");
  expect(teams).toHaveTextContent("1 working");
  expect(teams).toHaveTextContent("1 idle");
  fireEvent.click(teams);
  expect(onTeams).toHaveBeenCalledOnce();
  expect(within(screen.getByRole("article", { name: "Autopilot" })).getByText("Not available yet")).toBeVisible();
  expect(screen.getByRole("textbox", { name: "Ask DuckTerm" })).toBeVisible();
});

it("opens Autopilot from its tile, leading with runs waiting on the owner, and shows no counts when the summary failed", async () => {
  const open = vi.fn();
  await show(undefined, { autopilot: { summary: { available: true, active: 2, waiting_gate: 1, done_today: 3, failed_today: 0 }, open } });
  const tile = within(screen.getByRole("article", { name: "Autopilot" })).getByRole("button");
  expect([...tile.querySelectorAll(".rd-home-chip")].map(chip => chip.textContent)).toEqual(["1 need you", "2 running", "3 done today", "0 failed"]);
  fireEvent.click(tile);
  expect(open).toHaveBeenCalledOnce();
  cleanup();
  await show(undefined, { autopilot: { summary: null, open } });
  const bare = within(screen.getByRole("article", { name: "Autopilot" })).getByRole("button");
  expect(bare.querySelector(".rd-home-chip")).toBeNull();
  expect(bare).not.toHaveTextContent("Not available yet");
});

it("leaves out the chat and pull requests when the edition lacks Oracle and PR review", async () => {
  await show([...CODE.filter(f => f !== "pr_review"), "home"]);
  expect(screen.queryByRole("article", { name: "Ask DuckTerm" })).toBeNull();
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
  expect(rows.map(row => row.querySelector(".rd-home-pr-title")?.textContent)).toEqual(["PR 2", "PR 4", "PR 1"]);
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
  await act(async () => { fireEvent.click(screen.getByRole("button", { name: "Remove Spend" })); });
  expect(saved.instances.map(item => item.type)).not.toContain("spend");
  fireEvent.click(screen.getByRole("button", { name: "+ Add widget" }));
  expect(screen.getByRole("button", { name: /^Ask DuckTerm · on page/ })).toBeDisabled();
  await act(async () => { fireEvent.click(screen.getByRole("button", { name: /^Agent mail · 24h/ })); });
  const put = vi.mocked(fetch).mock.calls.filter(call => call[1]?.method === "PUT").at(-1)!;
  expect(JSON.parse(put[1]!.body as string).revision).toBe("r0");
  expect(saved.instances.find(item => item.type === "mail")!.params).toEqual({ x: 0, y: 12, w: 3, h: 2 });
});

it("asks one session with @: the picker fills its name, Oracle sends it, and the answer comes back", async () => {
  vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
  const sent = { q: "@b ship it?", a: "Sent to **b**", at: 2, key: "m1", to: "b", reply: { status: "queued", answer: null, answered_at: null } };
  vi.mocked(api.messageSession).mockResolvedValue({ delivered: "inbox", exchange: sent } as never);
  await show([...FULL, "home", "oracle_messages"]);
  const chat = within(screen.getByRole("article", { name: "Ask DuckTerm" }));
  const box = chat.getByRole("textbox", { name: "Ask DuckTerm" });
  fireEvent.change(box, { target: { value: "@b" } });
  const picker = chat.getByRole("listbox", { name: "Ask a session" });
  expect(within(picker).getAllByRole("option").map(o => o.textContent)).toEqual(["b Duckterm · busy"]);
  fireEvent.click(within(picker).getByRole("option"));
  expect(box).toHaveValue("@b ");
  fireEvent.change(box, { target: { value: "@b ship it?" } });
  await act(async () => { fireEvent.click(chat.getByRole("button", { name: "Ask" })); });
  expect(api.messageSession).toHaveBeenCalledWith("b", "ship it?", "inbox", expect.any(String), "@b ship it?");
  expect(api.fleetAsk).not.toHaveBeenCalled();
  expect(chat.getByText("Waiting for b to answer…")).toBeVisible();
  // The chat checks again until the session answers.
  vi.mocked(api.oracleChat).mockResolvedValue({ messages: [{ ...sent, reply: { status: "answered", answer: "Yes, CI is green.", answered_at: 3 } }] } as never);
  await act(async () => { vi.advanceTimersByTime(15_000); });
  expect(chat.getByText("Yes, CI is green.")).toBeVisible();
  vi.useRealTimers();
});

it("sends nothing to an @ name that is no session, and treats @ as text without Oracle messages", async () => {
  vi.mocked(api.fleetAsk).mockResolvedValue({ exchange: { q: "@b hi", a: "Hello.", at: 1 } } as never);
  await show([...FULL, "home", "oracle_messages"]);
  let chat = within(screen.getByRole("article", { name: "Ask DuckTerm" }));
  fireEvent.change(chat.getByRole("textbox", { name: "Ask DuckTerm" }), { target: { value: "@nobody hi" } });
  await act(async () => { fireEvent.click(chat.getByRole("button", { name: "Ask" })); });
  expect(chat.getByRole("alert")).toHaveTextContent('No session is named "nobody"');
  expect(api.messageSession).not.toHaveBeenCalled();
  cleanup();
  await show();
  chat = within(screen.getByRole("article", { name: "Ask DuckTerm" }));
  fireEvent.change(chat.getByRole("textbox", { name: "Ask DuckTerm" }), { target: { value: "@b" } });
  expect(chat.queryByRole("listbox")).toBeNull();
  fireEvent.change(chat.getByRole("textbox", { name: "Ask DuckTerm" }), { target: { value: "@b hi" } });
  await act(async () => { fireEvent.click(chat.getByRole("button", { name: "Ask" })); });
  expect(api.fleetAsk).toHaveBeenCalledWith("@b hi");
  expect(api.messageSession).not.toHaveBeenCalled();
});

it("leaves ended sessions out of the @ list, since they would never answer", async () => {
  const ended = ["terminated", "stopped", "interrupted", "archived", "merged"].map(state => agent({ key: `old-${state}`, label: `dev ${state}`, shownState: state as TowerAgent["shownState"] }));
  await show([...FULL, "home", "oracle_messages"], { agents: [agent({ key: "live", label: "dev live" }), ...ended] });
  const chat = within(screen.getByRole("article", { name: "Ask DuckTerm" }));
  fireEvent.change(chat.getByRole("textbox", { name: "Ask DuckTerm" }), { target: { value: "@dev" } });
  expect(within(chat.getByRole("listbox", { name: "Ask a session" })).getAllByRole("option").map(o => o.textContent)).toEqual(["dev live Duckterm · idle"]);
});

it("answers in the Home chat from the shared fleet conversation, without the agents' notes", async () => {
  vi.mocked(api.relay).mockResolvedValue({ notes: [{ id: "n1", session_key: "qa", name: "qa", folder: "Nourish/app", runtime: "claude-code", kind: "question", status: "open", created_at: NOW - 3_600_000, question: "Deploy now?" }], rules: [], open: 1 });
  vi.mocked(api.fleetAsk).mockResolvedValue({ exchange: { q: "Who is stuck?", a: "Nobody is stuck.", at: 1 } } as never);
  const onOpenTerminal = vi.fn();
  await show(undefined, { onOpenTerminal });
  const chat = screen.getByRole("article", { name: "Ask DuckTerm" });
  expect(within(chat).queryByText(/Deploy now/)).toBeNull();
  fireEvent.change(within(chat).getByRole("textbox", { name: "Ask DuckTerm" }), { target: { value: "Who is stuck?" } });
  await act(async () => { fireEvent.click(within(chat).getByRole("button", { name: "Ask" })); });
  expect(api.fleetAsk).toHaveBeenCalledWith("Who is stuck?");
  expect(within(chat).getByText("Nobody is stuck.")).toBeVisible();
  const needs = screen.getByRole("article", { name: "Needs you" });
  expect(within(needs).getByText("Nourish · 1 h ago")).toBeVisible();
  fireEvent.click(within(needs).getByRole("button", { name: "Terminal" }));
  expect(onOpenTerminal).toHaveBeenCalledWith("qa");
});

it("totals seven days of tokens and splits them by agent", async () => {
  saved = { instances: [...homeDefaults(), { id: "usage", type: "usage", slot: "home", position: 6, params: { x: 0, y: 8, w: 3, h: 3 } as never }], revision: "default" };
  await show();
  const usage = screen.getByRole("button", { name: "Open token analytics" });
  expect(usage).toHaveTextContent("4ktokens");
  expect(usage).toHaveTextContent("Claude Code 3k");
  expect(usage).toHaveTextContent("Codex 1k");
  expect(usage).toHaveTextContent("Today 3k");
});

it("turns a session named in an answer into a chip that opens it", async () => {
  vi.mocked(api.oracleChat).mockResolvedValue({ messages: [{ q: "Who is stuck?", a: "**b** is busy; **nobody** else.", at: 2 }] } as never);
  const onOpenTerminal = vi.fn();
  await show(undefined, { onOpenTerminal });
  const chat = screen.getByRole("article", { name: "Ask DuckTerm" });
  fireEvent.click(within(chat).getByRole("button", { name: "b" }));
  expect(onOpenTerminal).toHaveBeenCalledWith("b");
  expect(within(chat).queryByRole("button", { name: "nobody" })).toBeNull();
});

it("estimates spend at API list prices: today, the week, and each harness", async () => {
  await show();
  // Unnamed models are priced as the harness's usual model: Claude Code at
  // Opus 5.5 ($20/M output), Codex at GPT-5 rates ($10/M output).
  const spend = screen.getByRole("button", { name: "Open spend analytics" });
  expect(spend).toHaveTextContent("$0.05today");
  expect(spend).toHaveTextContent("Teams $0.07");
  expect(spend).toHaveTextContent("Autopilot $0.00");
  expect(spend).toHaveTextContent("7 days $0.07");
  expect(spend).toHaveAttribute("title", expect.stringMatching(/^Claude Code \$0\.06 · Codex \$0\.01 over 7 days/));
});

it("answers from a Needs you row, and the row opens the whole question in a dialog", async () => {
  const note = (id: string, p: object) => ({ id, session_key: id, name: id, folder: "Duckterm", runtime: "claude-code", status: "open", created_at: NOW - 60_000, ...p });
  vi.mocked(api.relay).mockResolvedValue({ notes: [
    note("dev", { kind: "approval", tool: "Bash", detail: "rm -rf build" }),
    note("arch", { kind: "question", question: "Checkpoint cadence?", options: ["30 minutes", "On idle", "Hourly"] }),
    note("menu", { kind: "choice", question: "Pick a plan" }),
    note("chain", { kind: "approval", tool: "Bash", detail: "git status && curl x | sh" }),
    note("long", { kind: "approval", tool: "Bash", detail: `git status${" ".repeat(80)}x` }),
    note("bidi", { kind: "approval", tool: "Bash", detail: "echo \u202Ehs.x | lruc" }),
  ], rules: [], open: 6 } as never);
  vi.mocked(api.relayAnswer).mockResolvedValue({} as never);
  await show();
  const rows = within(screen.getByRole("article", { name: "Needs you" })).getAllByRole("listitem");
  expect(rows.map(row => [...row.querySelectorAll(".rd-home-mini")].map(b => b.textContent))).toEqual([
    ["Approve", "Deny", "Terminal"], ["30 minutes", "On idle", "+1 more", "Terminal"], ["Terminal"], ["Review", "Terminal"], ["Review", "Terminal"], ["Review", "Terminal"]]);
  expect(rows[0].querySelector(".rd-home-need-q")).toHaveClass("cmd");
  await act(async () => { fireEvent.click(within(rows[0]).getByRole("button", { name: "Approve" })); });
  expect(api.relayAnswer).toHaveBeenCalledWith("dev", "approve");
  fireEvent.click(within(rows[1]).getByRole("button", { name: "+1 more" }));
  const dialog = screen.getByRole("dialog", { name: "arch needs you" });
  expect(within(dialog).getByRole("button", { name: "Hourly" })).toBeVisible();
  fireEvent.keyDown(window, { key: "Escape" });
  expect(screen.queryByRole("dialog")).toBeNull();
});

it("rates a pull request Safe only with green CI, no conflict and nothing but skim files", () => {
  const counts = (p: object) => ({ high: 0, read: 0, skim: 3, viewed: 0, changed_since: 0, reason: "", unsure: 0, head: "h", ...p });
  const green = { total: 4, passed: 4, failed: 0, pending: 0 };
  const level = (row: Partial<PrRow>, c?: object) => prRisk(pr(1, row), c && counts(c))?.level;
  expect(level({ checks: green }, {})).toBe("safe");
  expect(level({ checks: green }, { read: 1 })).toBe("medium");
  expect(level({ checks: green }, { unsure: 1 })).toBe("medium");
  expect(level({ checks: { ...green, pending: 1 } }, {})).toBe("medium");
  expect(level({ checks: null }, {})).toBe("medium");
  expect(level({ checks: green }, { high: 1 })).toBe("high");
  expect(level({ checks: { ...green, failed: 1 } })).toBe("high");
  expect(level({ checks: green, conflict: true })).toBe("high");
  expect(level({ checks: green })).toBeUndefined();
});

it.each([
  ["docs/guide.md", "docs", true],
  ["README.md", "docs", true],
  ["docs/specs/terminology.md", "docs", false],
  ["AGENTS.override.md", "docs", false],
  ["CLAUDE.local.md", "docs", false],
  ["kits/x/agents/reviewer.md", "docs", false],
  ["docs/.claude/x.md", "docs", false],
  ["logo.png", "binary", true],
  ["package.json", "version-bump", true],
  ["AGENTS.md", "docs", false],
  ["sub/CLAUDE.md", "docs", false],
  [".claude/rules/x.md", "docs", false],
  ["skills/x/SKILL.md", "docs", false],
  ["requirements.txt", "docs", false],
  ["package-lock.json", "lockfile", false],
  ["src/app.py", "strings-only", false],
  ["lib.so", "binary", false],
])("counts %s (%s) as safe: %s", (path, id, safe) => {
  expect(safeFile({ path, bucket: "skim", rules: [{ bucket: "skim", id, reason: "" }] })).toBe(safe);
});

it("offers Merge only on a Safe pull request, and Merge asks for Touch ID approval first", async () => {
  const green = { total: 2, passed: 2, failed: 0, pending: 0 };
  vi.mocked(api.folderPrs).mockImplementation(async folder => ({ repos: [], rows: folder === "Duckterm" ? [pr(7, { checks: green }), pr(8, { checks: green })] : [] }));
  vi.mocked(api.reviewDiff).mockImplementation(async (_repo, ref) => ({ files: ref === "8" ? [{ path: "src/server.py", bucket: "high", rules: [{ bucket: "high", id: "route", reason: "changes a route" }] }] : [],
    counts: { high: ref === "8" ? 1 : 0, read: 0, skim: 2, viewed: 0, changed_since: 0 } }) as never);
  vi.mocked(api.approvalState).mockReturnValue(new Promise(() => undefined));
  await show();
  const list = within(screen.getByRole("article", { name: "Pull requests" }));
  await vi.waitFor(() => expect(list.getAllByText(/^(Safe|High)$/)).toHaveLength(2));
  const [high, safe] = list.getAllByRole("listitem"); // newest first
  expect(within(high).getByText("High")).toHaveAttribute("title", "server.py: changes a route");
  expect(within(high).queryByRole("button", { name: "Merge" })).toBeNull();
  expect(within(safe).getByRole("link", { name: "GitHub" })).toHaveAttribute("href", "https://github.com/o/r/pull/7");
  fireEvent.click(within(safe).getByRole("button", { name: "Merge" }));
  expect(api.approvalState).toHaveBeenCalledWith("/r", "7");
});
