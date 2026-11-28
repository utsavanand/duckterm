import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { AutopilotPage, WorkspaceFlows } from "./Autopilot";
import { AutopilotOverview, AutopilotRunDetail } from "./autopilotApi";

const sent: { method: string; path: string; body: unknown }[] = [];
let overview: AutopilotOverview;

// What GET /api/autopilot/runs/<id> adds to a listed run (runs.detail).
function detail(id: string): AutopilotRunDetail {
  const run = overview.runs.find(r => r.id === id)!;
  return {
    ...run, ended_at: run.started_at + 376_000, tokens: 424_000, gates: [], loops: { check: 1 },
    attempts: [
      { step: "spec", attempt: 1, kind: "run", status: "completed", exit: 0, choice: null,
        reason: "I wrote the spec.\n\nIt moves colours into **variables**.", started_at: 1000, finished_at: 1047 },
      { step: "build", attempt: 1, kind: "run", status: "completed", exit: 1, choice: null,
        reason: "Tests failed in test_badge.py.", started_at: 1047, finished_at: 1324 },
    ],
    issue_body: "The badge stays after closing all items.\n\n![](https://tracker.example/p.gif)",
    spec: "# Spec: inbox badge\n\n## Problem\n\nThe count is read too early.",
    changes: {
      files: [{ path: "web/src/inbox.ts", added: 2, removed: 1, binary: false }],
      patch: "diff --git a/web/src/inbox.ts b/web/src/inbox.ts\nindex 1..2 100644\n--- a/web/src/inbox.ts\n+++ b/web/src/inbox.ts\n@@ -1,2 +1,3 @@\n-const n = list.length;\n+const n = answer.count;\n+badge(n);\n keep();\n",
      cut: false,
    },
  };
}

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
      : path.startsWith("/api/autopilot/runs/") ? detail(path.split("/").pop()!)
      : path.endsWith("/backlog") ? { issues: [7, 8] }
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
  fireEvent.change(screen.getByLabelText("Code repository"), { target: { value: "o/uv-suite" } });
  fireEvent.change(screen.getByLabelText("Workspace"), { target: { value: "Team" } });
  fireEvent.change(screen.getByLabelText("Labels"), { target: { value: "autopilot, bug ," } });
  await act(async () => { fireEvent.click(screen.getByRole("button", { name: "Next" })); });
  expect(screen.getByText("GitHub issues labelled autopilot, bug")).toBeInTheDocument();
  await act(async () => { fireEvent.click(screen.getByRole("button", { name: "Turn on" })); });
  const create = sent.find(r => r.method === "POST");
  expect(create?.body).toEqual({
    flow: "autopilot/issue-to-merge", name: "uv-suite issue to merge", workspace: "Team", repo: "o/uv-suite", issue_repo: "",
    triggers: { "github-issue": { labels: ["autopilot", "bug"] } },
    approvals: { spec_on_visible: false, keep_merge_approval: false }, budget_m: 5, max_runs: 2, test_command: "",
  });
});

it("takes issues from another repository and warns when another autopilot would pick up the same issue", async () => {
  overview.autopilots = [{ id: "ap-1", on: true, created_at: 0, flow: "autopilot/issue-to-merge", name: "duckterm issues", repo: "o/duckterm",
    workspace: "", triggers: { "github-issue": { labels: ["autopilot"] } }, approvals: { spec_on_visible: false, keep_merge_approval: false },
    budget_m: 5, max_runs: 2, active: 0, watch: {} }];
  await mount();
  await act(async () => { fireEvent.click(screen.getByRole("button", { name: "Set up" })); });
  await act(async () => { await new Promise(resolve => setTimeout(resolve, 10)); });
  fireEvent.change(screen.getByLabelText("Code repository"), { target: { value: "o/uv-suite" } });
  fireEvent.change(screen.getByLabelText("Issues from"), { target: { value: "o/duckterm" } });
  expect(screen.getByText(/duckterm issues also watches o\/duckterm for “autopilot”/)).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("Labels"), { target: { value: "uv" } });
  expect(screen.queryByText(/also watches/)).toBeNull();
  await act(async () => { fireEvent.click(screen.getByRole("button", { name: "Next" })); });
  expect(screen.getByText("· issues from o/duckterm")).toBeInTheDocument();
  await act(async () => { fireEvent.click(screen.getByRole("button", { name: "Turn on" })); });
  expect(sent.find(r => r.method === "POST")?.body).toMatchObject({ repo: "o/uv-suite", issue_repo: "o/duckterm" });
});

it("an edit that changes the labels asks whether open issues that now match should run", async () => {
  overview.autopilots = [{ id: "ap-1", on: true, created_at: 0, flow: "autopilot/issue-to-merge", name: "kaho reports", repo: "o/duckterm",
    workspace: "", triggers: { "github-issue": { labels: ["autopilot"] } }, approvals: { spec_on_visible: false, keep_merge_approval: false },
    budget_m: 5, max_runs: 2, active: 0, watch: {} }];
  await mount();
  await act(async () => { fireEvent.click(screen.getByRole("button", { name: "Edit" })); });
  await act(async () => { await new Promise(resolve => setTimeout(resolve, 10)); });
  await act(async () => { fireEvent.click(screen.getByRole("button", { name: "Next" })); });
  expect(sent.some(r => r.path.endsWith("/backlog"))).toBe(false);
  expect(screen.queryByText(/open issues/)).toBeNull();
  await act(async () => { fireEvent.click(screen.getByRole("button", { name: "Back" })); });
  fireEvent.change(screen.getByLabelText("Labels"), { target: { value: "autopilot, bug" } });
  await act(async () => { fireEvent.click(screen.getByRole("button", { name: "Next" })); });
  expect(sent.find(r => r.path.endsWith("/backlog"))?.body).toMatchObject({ triggers: { "github-issue": { labels: ["autopilot", "bug"] } } });
  expect(screen.getByText("2 open issues in o/duckterm match the new settings")).toBeInTheDocument();
  expect(screen.getByRole("radio", { name: /Only new ones/ })).toBeChecked();
  fireEvent.click(screen.getByRole("radio", { name: /Run them/ }));
  await act(async () => { fireEvent.click(screen.getByRole("button", { name: "Save" })); });
  expect(sent.find(r => r.method === "PATCH")?.body).toMatchObject({ backlog: "run", triggers: { "github-issue": { labels: ["autopilot", "bug"] } } });
});

it("cannot go past triggers with none turned on", async () => {
  await mount();
  await act(async () => { fireEvent.click(screen.getByRole("button", { name: "Set up" })); });
  fireEvent.click(screen.getByRole("checkbox", { name: /GitHub issues/ }));
  expect(screen.getByRole("button", { name: "Next" })).toBeDisabled();
  expect(screen.getByText("Turn on at least one trigger")).toBeInTheDocument();
});

async function openRow(title: string) {
  await act(async () => { fireEvent.click(screen.getByText(title).closest("tr")!); });
  await act(async () => { await new Promise(resolve => setTimeout(resolve, 10)); });
}

it("a run row opens the run: each step with its time and output, the error and the issue", async () => {
  await mount();
  expect(within(screen.getByText("Inbox badge stays").closest("tr")!).getByText("Failed")).toBeInTheDocument();
  await openRow("Inbox badge stays");
  expect(sent.map(r => r.path)).toContain("/api/autopilot/runs/r1");
  expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("#41 Inbox badge stays");
  expect(screen.getByRole("alert")).toHaveTextContent("Docker is not running");
  expect(screen.getByRole("link", { name: "Issue #41" })).toHaveAttribute("href", "https://github.com/o/duckterm/issues/41");
  expect(screen.getByText("6 min 16 s")).toBeInTheDocument();
  expect(screen.getByText("424k")).toBeInTheDocument();

  const [spec, build] = within(screen.getByRole("list")).getAllByRole("button");
  expect(spec).toHaveTextContent("specClaude CodeI wrote the spec.47 s");
  expect(build).toHaveTextContent("4 min 37 s");
  expect(build).toHaveClass("ap-sel");
  expect(screen.getByText("Tests failed in test_badge.py.", { selector: ".ap-doc p" })).toBeInTheDocument();
  await act(async () => { fireEvent.click(spec); });
  expect(screen.getByText("variables", { selector: "strong" })).toBeInTheDocument();

  await act(async () => { fireEvent.click(screen.getByRole("tab", { name: "Issue" })); });
  expect(screen.getByText("The badge stays after closing all items.")).toBeInTheDocument();
  // an outside author wrote the issue: opening the run must not fetch their image
  expect(document.body.innerHTML).not.toContain("tracker.example");
  await act(async () => { fireEvent.click(screen.getByRole("button", { name: "← Autopilot" })); });
  expect(screen.getByText("Badge colour")).toBeInTheDocument();
});

it("a run's spec and changes tabs show the spec it wrote and its diff", async () => {
  await mount();
  await openRow("Badge colour");
  expect(screen.getByRole("link", { name: "Draft pull request #450" })).toHaveAttribute("href", "https://github.com/o/duckterm/pull/450");
  expect(screen.getByText(/on GitHub/)).toBeInTheDocument();
  await act(async () => { fireEvent.click(screen.getByRole("tab", { name: "Spec" })); });
  expect(screen.getByRole("heading", { name: "Problem" })).toBeInTheDocument();
  await act(async () => { fireEvent.click(screen.getByRole("tab", { name: /^Changes/ })); });
  expect(screen.getByText("const n = answer.count;", { exact: false })).toHaveClass("ap-da");
  expect(screen.getByText("-const n = list.length;")).toHaveClass("ap-dd");
  expect(screen.getByText("@@ -1,2 +1,3 @@")).toHaveClass("ap-dh");
  expect(screen.queryByText(/^index /)).toBeNull();
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

function waitingRun() {
  overview.runs.unshift({ id: "r3", autopilot: "ap-1", autopilot_name: "duckterm issues", workspace: "Team", flow: "autopilot/issue-to-merge",
    repo: "o/duckterm", trigger: "github-issue", item: { number: 43, title: "Row stuck" }, branch: "autopilot/r3", state: "waiting_gate",
    started_at: Date.now(), classification: { surfaces: ["server"], security: { level: "touches", areas: ["auth"] }, ai: "deterministic", data: "none" },
    gate: { step: "spec-gate", question: "Approve the fix spec?", choices: ["approve", "revise", "reject"], shown: "# Fix",
      why: "security touches auth", asked_at: Date.now() - 12 * 60_000 } });
}

it("a spec waiting for approval says why the policy asked, and approving answers that gate", async () => {
  waitingRun();
  await mount();
  const card = screen.getByText("Spec approval").closest("article")!;
  expect(within(card).getByRole("button", { name: "#43 Row stuck" })).toBeInTheDocument();
  expect(card).toHaveTextContent("Required because: security touches auth. No code written yet; the sandbox is stopped.");
  expect(within(card).getByText("security")).toHaveClass("ap-risk-sec");
  expect(within(card).getByText("waiting 12 min")).toBeInTheDocument();
  await act(async () => { fireEvent.click(within(card).getByRole("button", { name: "Approve" })); });
  expect(sent.find(r => r.method === "POST")).toEqual({ method: "POST", path: "/api/autopilot/runs/r3/gate", body: { step: "spec-gate", answer: "approve" } });
});

it("rejecting a spec sends it back with the note, or ends the run", async () => {
  waitingRun();
  await mount();
  const card = screen.getByText("Spec approval").closest("article")!;
  fireEvent.click(within(card).getByRole("button", { name: "Reject with note" }));
  const send = screen.getByRole("button", { name: "Send back" });
  expect(send).toBeDisabled();
  fireEvent.change(screen.getByLabelText("Note for the spec step"), { target: { value: "Keep the cap." } });
  await act(async () => { fireEvent.click(send); });
  expect(sent.filter(r => r.method === "POST").map(r => r.body)).toEqual([{ step: "spec-gate", answer: "revise", note: "Keep the cap." }]);
});

it("a run's steps name what ran each one: Claude Code, Codex, a script or you", async () => {
  await mount();
  overview.runs[0].state = "succeeded";
  const base = detail("r1");
  const at = { status: "completed", exit: 0, choice: null, reason: "", started_at: 1, finished_at: 2 };
  const attempts = [
    { ...at, step: "spec", attempt: 1, kind: "run", harness: "claude-code", reason: "Raised `CLOSE_TIMEOUT` in **audio_capture.py**." },
    { ...at, step: "tests", attempt: 1, kind: "exec", harness: null },
    { ...at, step: "qa", attempt: 1, kind: "decide", harness: "codex" },
    { ...at, step: "spec-gate", attempt: 1, kind: "gate", harness: null },
    { ...at, step: "checks", attempt: 1, kind: "parallel", harness: null },
  ];
  vi.mocked(fetch).mockImplementation(async (input: RequestInfo | URL) => new Response(JSON.stringify(
    String(input).startsWith("/api/autopilot/runs/") ? { ...base, attempts } : overview), { status: 200 }));
  await act(async () => { fireEvent.click(screen.getAllByRole("row")[1]); });
  await act(async () => { await new Promise(resolve => setTimeout(resolve, 10)); });
  const labels = Array.from(document.querySelectorAll(".ap-attempts .ap-att-kind")).map(e => e.textContent);
  expect(labels).toEqual(["Claude Code", "script", "Codex · decides", "your approval", "runs the reviews together"]);
  expect(screen.getByText("Raised CLOSE_TIMEOUT in audio_capture.py.")).toBeInTheDocument();
});

it("Read spec opens the run on its Spec tab", async () => {
  waitingRun();
  await mount();
  await act(async () => { fireEvent.click(screen.getByRole("button", { name: "Read spec" })); });
  await act(async () => { await new Promise(resolve => setTimeout(resolve, 10)); });
  expect(screen.getByRole("tab", { name: "Spec" })).toHaveAttribute("aria-selected", "true");
});

it("shows no Needs you section when nothing waits", async () => {
  await mount();
  expect(screen.queryByRole("heading", { name: /Needs you/ })).toBeNull();
});
