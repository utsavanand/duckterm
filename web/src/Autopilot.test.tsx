import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { AutopilotPage } from "./Autopilot";
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
    runs: [{ id: "r1", autopilot: "ap-1", autopilot_name: "duckterm issues", flow: "autopilot/issue-to-merge", repo: "o/duckterm",
      trigger: "github-issue", item: { number: 41, title: "Inbox badge stays", url: "https://github.com/o/duckterm/issues/41", started_by: "maintainer" },
      branch: "autopilot/r1", state: "failed", started_at: Date.now(), error: "Docker is not running", steps: [{ step: "spec", attempt: 1, exit: 0, choice: null }] }],
    summary: { available: true, active: 0, waiting_gate: 0, done_today: 0, failed_today: 1 },
    problems: [],
  };
  vi.stubGlobal("fetch", vi.fn(async (input: string, init?: RequestInit) => {
    const path = String(input), method = init?.method ?? "GET";
    sent.push({ method, path, body: init?.body ? JSON.parse(String(init.body)) : undefined });
    const body = path === "/api/autopilot" ? overview
      : path === "/api/autopilot/repositories" ? ["o/duckterm", "o/uv-suite"]
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

it("puts a flow on autopilot with only the triggers that exist, and labels as a list", async () => {
  await mount();
  await act(async () => { fireEvent.click(screen.getAllByRole("button", { name: /Put a flow on autopilot/ })[0]); });
  await act(async () => { fireEvent.click(screen.getByRole("button", { name: /Issue to merge/ })); });
  await act(async () => { await new Promise(resolve => setTimeout(resolve, 10)); });
  expect(screen.getByRole("checkbox", { name: /GitHub issues/ })).toBeChecked();
  expect(screen.getByRole("checkbox", { name: /Jira issues/ })).toBeDisabled();
  expect(screen.getByRole("checkbox", { name: /Slack messages/ })).toBeDisabled();
  expect(screen.queryByText(/by hand/i)).toBeNull();
  fireEvent.change(screen.getByRole("combobox"), { target: { value: "o/uv-suite" } });
  fireEvent.change(screen.getByLabelText("Labels"), { target: { value: "autopilot, bug ," } });
  await act(async () => { fireEvent.click(screen.getByRole("button", { name: "Next" })); });
  expect(screen.getByText("GitHub issues labelled autopilot, bug")).toBeInTheDocument();
  await act(async () => { fireEvent.click(screen.getByRole("button", { name: "Put on autopilot" })); });
  const create = sent.find(r => r.method === "POST");
  expect(create?.body).toEqual({
    flow: "autopilot/issue-to-merge", name: "uv-suite issue to merge", repo: "o/uv-suite",
    triggers: { "github-issue": { labels: ["autopilot", "bug"] } },
    approvals: { spec_on_visible: false, keep_merge_approval: false }, budget_m: 5, max_runs: 2,
  });
});

it("cannot go past triggers with none turned on", async () => {
  await mount();
  await act(async () => { fireEvent.click(screen.getAllByRole("button", { name: /Put a flow on autopilot/ })[0]); });
  await act(async () => { fireEvent.click(screen.getByRole("button", { name: /Issue to merge/ })); });
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
