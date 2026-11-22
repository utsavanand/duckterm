import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { api, KitInventory, KitPlan, KitsOverview } from "./api";
import { SkillKitsPage } from "./SkillKitsPage";

vi.mock("./api", () => ({
  api: { kitsOverview: vi.fn(), kitsPlan: vi.fn(), kitsInstall: vi.fn(), kitsUninstall: vi.fn(), kitsVerify: vi.fn(), kitsRun: vi.fn() },
}));
afterEach(() => { cleanup(); vi.resetAllMocks(); });

const procedure = {
  name: "uv-coding", description: "", template: "teams/uv-coding.toml",
  roles: { architect: { purpose: "", skills: ["uvs-spec"] }, dev: { purpose: "", skills: ["uvs-prototype"] } },
  rules: [{ role: "dev", review_by: "qa" }, { role: "dev", may_not: ["push"] }],
  kit: "uv-suite", kit_version: "0.34.0",
};
const overview: KitsOverview = {
  project: "/ws/duckterm",
  kits: [{
    name: "uv-suite", version: "0.34.0", harnesses: ["claude-code", "codex"], options: { persona: "professional" },
    installed_at: 1, entries: 131, trusted: true, verify: { ok: true, missing: [], drifted: [] },
    flows: ["review", "feature"], components: ["uv-suite/skill/uvs-spec", "uv-suite/hook/block_destructive", "uv-suite/flow/review"],
    procedures: [procedure],
  }],
  procedures: [procedure],
  harnesses_present: { "claude-code": true, codex: true, copilot: false, opencode: false },
  runs: [],
};
const inventory: KitInventory = {
  name: "sample-kit", version: "0.1.0", description: "One of every component kind", root: "/kits/sample",
  options: { persona: { default: "professional", choices: ["professional", "sport"] } },
  components: { skill: [{ name: "review-diff" }, { name: "raise-pr" }], hook: [{ name: "stop-remind-tests" }] },
  component_count: 3, procedures: [], findings: [{ level: "warning", component: "x", message: "w" }], valid: true,
};
const plan: KitPlan = {
  kit: { name: "sample-kit", version: "0.1.0" }, project: "/ws/duckterm", harnesses: ["claude-code"], options: { persona: "professional" },
  findings: [], conflicts: [{ path: ".claude/agents/reviewer.md", kind: "file-exists", detail: "exists and is not from a kit", choices: ["keep", "replace"], component: "sample-kit/agent/reviewer" }],
  rows: [
    { component: "sample-kit/skill/review-diff", kind: "skill", harnesses: { "claude-code": "installed" }, notes: ["read by claude-code"] },
    { component: "sample-kit/hook/stop-remind-tests", kind: "hook", harnesses: { "claude-code": "installed" }, notes: [] },
  ],
  files: [{ path: ".claude/skills/review-diff", op: "copy-tree", harness: "shared", component: "sample-kit/skill/review-diff" }],
};

it("lists installed kits with their verify state and the procedures they ship", async () => {
  vi.mocked(api.kitsOverview).mockResolvedValue(overview);
  render(<SkillKitsPage defaultDir="/ws/duckterm" knownDirs={["/ws/duckterm"]} onClose={() => {}} fallback={<div>legacy</div>} />);
  expect(await screen.findByText("uv-suite")).toBeVisible();
  expect(screen.getByText(/verified · 131 entries/)).toBeVisible();
  expect(screen.getByText("uv-coding")).toBeVisible();
  expect(screen.getByText(/dev's work is reviewed by qa · dev may not push/)).toBeVisible();
  expect(api.kitsOverview).toHaveBeenCalledWith("/ws/duckterm", "");
  expect(screen.queryByText("legacy")).toBeNull();
});

it("falls back to the legacy modal when the flag is off", async () => {
  vi.mocked(api.kitsOverview).mockRejectedValue(new Error("skill_kits is still in development; see docs/feature-flags.md"));
  render(<SkillKitsPage defaultDir="/ws/duckterm" knownDirs={[]} onClose={() => {}} fallback={<div>legacy</div>} />);
  expect(await screen.findByText("legacy")).toBeVisible();
});

it("walks add → review → install and lands on the installed kit", async () => {
  vi.mocked(api.kitsOverview).mockResolvedValue({ ...overview, kits: [], procedures: [] });
  vi.mocked(api.kitsPlan).mockResolvedValue({ inventory, plan });
  vi.mocked(api.kitsInstall).mockResolvedValue({ plan, report: { kit: "sample-kit", written: ["a", "b"], removed: [], kept: [], notes: [] } });
  render(<SkillKitsPage defaultDir="/ws/duckterm" knownDirs={["/ws/duckterm"]} onClose={() => {}} fallback={null} />);
  fireEvent.click(await screen.findByRole("button", { name: "Add skill kit…" }));
  fireEvent.change(screen.getByLabelText(/Kit folder/), { target: { value: "/kits/sample" } });
  await waitFor(() => expect(api.kitsPlan).toHaveBeenCalled());
  expect(await screen.findByText("validate: ok")).toBeVisible();
  expect(screen.getByText(/skill · 2/)).toBeVisible();
  expect(screen.getByLabelText("Option · persona")).toHaveValue("professional");
  fireEvent.click(screen.getByRole("button", { name: "Review what will change" }));
  expect(await screen.findByText(/Review: sample-kit 0.1.0/)).toBeVisible();
  expect(screen.getAllByText("installed")).toHaveLength(2);
  expect(screen.getByText(/1 soft · 0 hard/)).toBeVisible();
  const installed: KitsOverview = { ...overview, kits: [{ ...overview.kits[0], name: "sample-kit", version: "0.1.0", flows: ["review"], procedures: [] }], procedures: [] };
  vi.mocked(api.kitsOverview).mockResolvedValue(installed);
  fireEvent.click(screen.getByRole("button", { name: /Install 2 components/ }));
  await waitFor(() => expect(api.kitsInstall).toHaveBeenCalledWith(expect.objectContaining({ kit: "/kits/sample", project: "/ws/duckterm", replace: false }), ""));
  expect(await screen.findByRole("heading", { name: /sample-kit/ })).toBeVisible();
  expect(screen.getByRole("button", { name: "Run" })).toBeVisible();
});
