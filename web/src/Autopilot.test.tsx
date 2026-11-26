import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { AutopilotPage, WorkspaceFlows } from "./Autopilot";
import { AutopilotOverview } from "./autopilotApi";

const sent: { method: string; path: string; body: unknown }[] = [];
let overview: AutopilotOverview;

beforeEach(() => {
  sent.length = 0;
  overview = {
    flows: [{ id: "autopilot/issue-to-merge", kit: "autopilot", name: "issue-to-merge", description: "Specs and builds a fix.",
      triggers: ["github-issue", "jira-issue", "slack-message"], gated: false }],
    triggers: {
      "github-issue": { needs: "github", available: true }, "github-pr": { needs: "github", available: false },
      "github-ci": { needs: "github", available: false }, "jira-issue": { needs: "jira", available: false },
      "slack-message": { needs: "slack", available: false }, schedule: { needs: null, available: false },
    },
    autopilots: [],
    runs: [{ id: "r1", autopilot: "ap-1", autopilot_name: "duckterm issues", workspace: "Team", flow: "autopilot/issue-to-merge", repo: "o/duckterm",
      trigger: "github-issue", item: { number: 41, title: "Inbox badge stays", url: "https://github.com/o/duckterm/issues/41", started_by: "maintainer" },
      branch: "autopilot/r1", state: "failed", started_at: Date.now(), error: "Docker is not running", steps: [{ step: "spec", attempt: 1, exit: 0, choice: null }] },
    { id: "r2", autopilot: "ap-1", autopilot_name: "duckterm issues", workspace: "Team", flow: "autopilot/issue-to-merge", repo: "o/duckterm",
      trigger: "github-issue", item: { number: 42, title: "Badge colour" }, branch: "autopilot/r2", state: "succeeded", started_at: Date.now() - 1000,
      pushed: true, pr: { number: 450, url: "https://github.com/o/duckterm/pull/450", base: "main" } }],
    summary: { available: true, active: 0, waiting_gate: 0, done_today: 0, failed_today: 1 },
    problems: [],
  };
  vi.stubGlobal("fetch", vi.fn(async (input: string, init?: RequestInit) => {
    const path = String(input), method = init?.method ?? "GET";
    sent.push({ method, path, body: init?.body ? JSON.parse(String(init.body)) : undefined });
    const body = path === "/api/autopilot" || path.startsWith("/api/autopilot?workspace=") ? overview
      : path === "/api/autopilot/repositories" ? ["o/duckterm", "o/uv-suite"]
      : path === "/folders" ? { folders: ["Team", "Docs"] }
      : method === "POST" && path === "/api/autopilot/autopilots" ? { id: "ap-2" }
      : {};
    return new Response(JSON.stringify(body), { status: 200 });
  }));
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

async function mount() {
  render(<AutopilotPage />);
  await act(async () => { await new Promise(resolve => setTimeout(resolve, 10)); });
}

it("sets up a listed flow with only the triggers that exist, and labels as a list", async () => {
  await mount();
  await act(async () => { fireEvent.click(screen.getByRole("button", { name: "Set up" })); });
  await act(async () => { await new Promise(resolve => setTimeout(resolve, 10)); });
  expect(screen.getByRole("checkbox", { name: /GitHub issues/ })).toBeChecked();
  expect(screen.getByRole("checkbox", { name: /Jira issues/ })).toBeDisabled();
  expect(screen.getByRole("checkbox", { name: /Slack messages/ })).toBeDisabled();
  expect(screen.queryByText(/by hand/i)).toBeNull();
  fireEvent.change(screen.getByLabelText("Repository"), { target: { value: "o/uv-suite" } });
  fireEvent.change(screen.getByLabelText("Workspace"), { target: { value: "Team" } });
  fireEvent.change(screen.getByLabelText("Labels"), { target: { value: "autopilot, bug ," } });
  await act(async () => { fireEvent.click(screen.getByRole("button", { name: "Next" })); });
  expect(screen.getByText("GitHub issues labelled autopilot, bug")).toBeInTheDocument();
  await act(async () => { fireEvent.click(screen.getByRole("button", { name: "Turn on" })); });
  const create = sent.find(r => r.method === "POST");
  expect(create?.body).toEqual({
    flow: "autopilot/issue-to-merge", name: "uv-suite issue to merge", workspace: "Team", repo: "o/uv-suite",
    triggers: { "github-issue": { labels: ["autopilot", "bug"] } },
    approvals: { spec_on_visible: false, keep_merge_approval: false }, budget_m: 5, max_runs: 2,
  });
});

it("cannot go past triggers with none turned on", async () => {
  await mount();
  await act(async () => { fireEvent.click(screen.getByRole("button", { name: "Set up" })); });
  fireEvent.click(screen.getByRole("checkbox", { name: /GitHub issues/ }));
  expect(screen.getByRole("button", { name: "Next" })).toBeDisabled();
  expect(screen.getByText("Turn on at least one trigger")).toBeInTheDocument();
});

it("a run row opens to its steps, its error and the issue", async () => {
  await mount();
  const row = screen.getByText("Inbox badge stays").closest("tr")!;
  expect(within(row).getByText("Failed")).toBeInTheDocument();
  await act(async () => { fireEvent.click(row); });
  expect(screen.getByText("Docker is not running")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Open the issue" })).toHaveAttribute("href", "https://github.com/o/duckterm/issues/41");
});

it("a run that opened a pull request says so and links it", async () => {
  await mount();
  const row = screen.getByText("Badge colour").closest("tr")!;
  expect(within(row).getByText("PR open")).toBeInTheDocument();
  await act(async () => { fireEvent.click(row); });
  expect(screen.getByRole("link", { name: "Draft pull request #450" })).toHaveAttribute("href", "https://github.com/o/duckterm/pull/450");
  expect(screen.getByText(/on GitHub/)).toBeInTheDocument();
});

it("a workspace's Flows tab asks for that workspace only and lists its runs", async () => {
  render(<WorkspaceFlows workspace="Team/web" />);
  await act(async () => { await new Promise(resolve => setTimeout(resolve, 10)); });
  expect(sent.map(r => r.path)).toContain("/api/autopilot?workspace=Team%2Fweb");
  expect(screen.getByText("Inbox badge stays")).toBeInTheDocument();
  expect(screen.getByText(/None in this workspace/)).toBeInTheDocument();
});

it("an empty page lists the flows to set up and shows no counts, runs or second call to action", async () => {
  overview.runs = [];
  overview.summary = { available: true, active: 0, waiting_gate: 0, done_today: 0, failed_today: 0 };
  await mount();
  expect(screen.getByRole("heading", { name: "Flows" })).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: /Issue to merge/ })).toBeInTheDocument();
  expect(screen.getAllByRole("button", { name: "Set up" })).toHaveLength(1);
  expect(screen.queryByRole("heading", { name: /On autopilot/ })).toBeNull();
  expect(screen.queryByRole("heading", { name: "Runs" })).toBeNull();
  expect(screen.queryByText(/need you/)).toBeNull();
  expect(screen.queryByText(/Put a flow on autopilot/)).toBeNull();
});

it("a page in use leads with counts, the waiting count marked, and lists each autopilot with its actions", async () => {
  overview.summary = { available: true, active: 1, waiting_gate: 2, done_today: 3, failed_today: 1 };
  overview.autopilots = [{ id: "ap-1", on: true, created_at: 0, flow: "autopilot/issue-to-merge", name: "duckterm issues", repo: "o/duckterm",
    workspace: "Team", triggers: { "github-issue": { labels: ["autopilot"] } }, approvals: { spec_on_visible: false, keep_merge_approval: false },
    budget_m: 5, max_runs: 2, active: 0, watch: { checked_at: Date.now() - 60_000 } }];
  await mount();
  expect(screen.getByText("need you").closest(".ap-pill")).toHaveClass("ap-pill-wait");
  expect(screen.getByText("failed today").closest(".ap-pill")).toHaveClass("ap-pill-bad");
  const row = screen.getByRole("heading", { name: /duckterm issues/ }).closest(".ap-item") as HTMLElement;
  expect(within(row).getByText("GitHub issues labelled autopilot")).toBeInTheDocument();
  expect(within(row).getAllByRole("button").map(b => b.textContent)).toEqual(["Run once…", "Pause", "Edit"]);
  await act(async () => { fireEvent.click(within(row).getByRole("button", { name: "Pause" })); });
  expect(sent.find(r => r.method === "PATCH")).toMatchObject({ path: "/api/autopilot/autopilots/ap-1", body: { on: false } });
});
