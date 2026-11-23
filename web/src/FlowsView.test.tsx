import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { api, KitRun, KitRunSummary } from "./api";
import { FlowsView } from "./FlowsView";

vi.mock("./api", () => ({ api: { kitsRuns: vi.fn(), kitsRunDetail: vi.fn(), kitsGate: vi.fn() } }));
afterEach(() => { cleanup(); vi.resetAllMocks(); });

const now = Math.floor(Date.now() / 1000);
const waiting: KitRunSummary = { run_id: "r-1", flow: "uv-suite/feature", state: "waiting_gate", started_at: now - 240, current: { step: "signoff", attempt: 1 }, failure: null, inputs: { topic: "CSV export" }, session: "s1", steps_done: 1 };
const running: KitRunSummary = { run_id: "r-2", flow: "uv-suite/review", state: "running", started_at: now - 40, current: { step: "verdict", attempt: 1 }, failure: null, inputs: {}, session: "s1", steps_done: 0 };
const run: KitRun = {
  ...waiting, harness: "claude-code", executor: "local",
  attempts: [{ step: "spec", attempt: 1, kind: "run", status: "completed", choice: null, reason: "wrote it", outputs: { spec: { path: ".duckterm/runs/r-1/spec/1/spec.md", sha256: "x" } } }],
  gates: [{ step: "signoff", visit: 1, question: "Approve the spec?", show: { path: ".duckterm/runs/r-1/spec/1/spec.md", sha256: "x" }, choices: ["approve", "reject"], response: null }],
};
const steps = [
  { id: "spec", kind: "run", skill: "uvs-spec" }, { id: "signoff", kind: "gate", question: "Approve the spec?", routes: { approve: "plan", reject: "spec" } },
  { id: "plan", kind: "run", skill: "uvs-architect" },
];

it("lists this session's runs with their steps and answers a gate under its step", async () => {
  vi.mocked(api.kitsRuns).mockResolvedValue({ runs: [running, waiting] });
  vi.mocked(api.kitsRunDetail).mockImplementation(async (_p, id) => id === "r-1" ? { run, steps } : { run: { ...run, ...running, attempts: [], gates: [] }, steps: [{ id: "verdict", kind: "decide", skill: "uvs-review" }] });
  vi.mocked(api.kitsGate).mockResolvedValue({ run_id: "r-1", resumed: true });
  render(<FlowsView project="/ws/duckterm" sessionKey="s1" context="s1" />);
  expect(await screen.findByText("feature")).toBeVisible();
  expect(screen.getByText("review")).toBeVisible();
  expect(screen.getAllByText("waiting for you").length).toBeGreaterThan(0);
  expect(await screen.findByText("Approve the spec?")).toBeVisible();
  expect(screen.getByText("spec.md")).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "approve" }));
  await waitFor(() => expect(api.kitsGate).toHaveBeenCalledWith("/ws/duckterm", "r-1", "signoff", "approve", "s1"));
});

it("shows a run file this machine did not write without a gate to answer", async () => {
  vi.mocked(api.kitsRuns).mockResolvedValue({ runs: [{ ...waiting, recorded: false }] });
  vi.mocked(api.kitsRunDetail).mockResolvedValue({ run, steps });
  render(<FlowsView project="/ws/duckterm" sessionKey="s1" context="s1" />);
  expect(await screen.findByText("feature")).toBeVisible();
  expect(screen.getByText(/not started on this machine/)).toBeVisible();
  await waitFor(() => expect(api.kitsRunDetail).toHaveBeenCalled());
  expect(screen.queryByRole("button", { name: "approve" })).toBeNull();
});

it("says so when no flow has run", async () => {
  vi.mocked(api.kitsRuns).mockResolvedValue({ runs: [] });
  render(<FlowsView project="/ws/duckterm" sessionKey="s1" />);
  expect(await screen.findByText(/No flow has run in this repository yet/)).toBeVisible();
});
