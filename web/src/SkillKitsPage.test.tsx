import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { api, KitCatalog, KitEntry, KitPlan } from "./api";
import { SkillKitsPage } from "./SkillKitsPage";

vi.mock("./api", () => ({
  api: { browse: vi.fn(), kitsCatalog: vi.fn(), kitsRegister: vi.fn(), kitsUnregister: vi.fn(), kitsPlan: vi.fn(), kitsInstall: vi.fn(), kitsUninstall: vi.fn(), kitsVerify: vi.fn(), kitsCreateTeam: vi.fn() },
}));
afterEach(() => { cleanup(); vi.resetAllMocks(); });

const procedure = {
  name: "uv-coding", description: "", template: "teams/uv-coding.toml",
  roles: { architect: { purpose: "", skills: ["uvs-spec"] }, dev: { purpose: "", skills: ["uvs-prototype"] } },
  rules: [{ role: "dev", review_by: "qa" }, { role: "dev", may_not: ["push"] }],
};
const uvSuite: KitEntry = {
  name: "uv-suite", path: "/Users/me/ws-uv-suite", version: "0.34.0", description: "Spec, architecture, review and test workflows",
  inventory: { name: "uv-suite", version: "0.34.0", description: "", root: "/Users/me/ws-uv-suite", options: {}, components: { skill: [{ name: "uvs-spec" }] }, component_count: 42, procedures: [procedure], findings: [], valid: true },
  details: { skill: [{ name: "uvs-spec", description: "Convert requirements into a structured technical specification.", effects: "workspace" }], flow: [{ name: "review", description: "Review the diff", inputs: [], trigger: null }] },
  procedures: [procedure], flows: ["review"], runs: [],
  installed_in: [{ workspace: "duckterm", project: "/ws/duckterm", version: "0.34.0", harnesses: ["claude-code", "codex"], installed_at: 1, trusted: true, verify: { ok: true, missing: [], drifted: [] } }],
};
const sample: KitEntry = {
  name: "sample-kit", path: "/kits/sample", version: "0.1.0", description: "One of every component kind",
  inventory: { name: "sample-kit", version: "0.1.0", description: "", root: "/kits/sample", options: {}, components: { skill: [{ name: "review-diff" }], hook: [{ name: "stop-remind-tests" }] }, component_count: 2, procedures: [], findings: [], valid: true },
  details: { skill: [{ name: "review-diff", description: "Reviews the diff." }] }, procedures: [], flows: [], runs: [], installed_in: [],
};
const catalog: KitCatalog = {
  kits: [uvSuite],
  workspaces: [{ name: "duckterm", repositories: ["/ws/duckterm"] }, { name: "payments", repositories: ["/ws/payments"] }, { name: "", repositories: [] }],
  harnesses: ["claude-code", "codex", "copilot", "opencode"],
  harnesses_present: { "claude-code": true, codex: true, copilot: false, opencode: false },
};
const plan: KitPlan = {
  kit: { name: "sample-kit", version: "0.1.0" }, project: "/ws/payments", harnesses: ["claude-code", "codex", "copilot", "opencode"], options: {},
  findings: [], conflicts: [{ path: ".claude/agents/reviewer.md", kind: "file-exists", detail: "exists and is not from a kit", choices: ["keep", "replace"], component: "sample-kit/agent/reviewer" }],
  rows: [
    { component: "sample-kit/skill/review-diff", kind: "skill", harnesses: { "claude-code": "installed", codex: "installed", copilot: "installed", opencode: "installed" }, notes: [] },
    { component: "sample-kit/hook/stop-remind-tests", kind: "hook", harnesses: { "claude-code": "installed", codex: "installed, needs trust", copilot: "installed", opencode: "not supported" }, notes: [] },
  ],
  files: [{ path: ".claude/skills/review-diff", op: "copy-tree", harness: "shared", component: "sample-kit/skill/review-diff" }],
};

it("lists added kits with where they are installed and the teams they offer", async () => {
  vi.mocked(api.kitsCatalog).mockResolvedValue(catalog);
  render(<SkillKitsPage onClose={() => {}} fallback={<div>legacy</div>} />);
  expect(await screen.findByText("uv-suite")).toBeVisible();
  expect(screen.getByText("duckterm")).toBeVisible();
  expect(screen.getByText("verified")).toBeVisible();
  expect(screen.getByText("uv-coding")).toBeVisible();
  expect(screen.getByText(/dev's work is reviewed by qa · dev may not push/)).toBeVisible();
  expect(screen.queryByText("legacy")).toBeNull();
  expect(screen.queryByLabelText(/repository/i)).toBeNull();
});

it("falls back to the legacy modal when the flag is off", async () => {
  vi.mocked(api.kitsCatalog).mockRejectedValue(new Error("skill_kits is still in development; see docs/feature-flags.md"));
  render(<SkillKitsPage onClose={() => {}} fallback={<div>legacy</div>} />);
  expect(await screen.findByText("legacy")).toBeVisible();
});

it("adds a kit from a path and lands on its page, not installed anywhere", async () => {
  vi.mocked(api.kitsCatalog).mockResolvedValueOnce(catalog).mockResolvedValue({ ...catalog, kits: [uvSuite, sample] });
  vi.mocked(api.kitsRegister).mockResolvedValue({ name: "sample-kit", path: "/kits/sample", version: "0.1.0", description: "" });
  render(<SkillKitsPage onClose={() => {}} fallback={null} />);
  fireEvent.click(await screen.findByRole("button", { name: "Add skill kit…" }));
  fireEvent.change(screen.getByLabelText("Kit folder"), { target: { value: "/kits/sample/skill-kit.toml" } });
  fireEvent.click(screen.getByRole("button", { name: "Add" }));
  await waitFor(() => expect(api.kitsRegister).toHaveBeenCalledWith("/kits/sample/skill-kit.toml", ""));
  expect(await screen.findByRole("heading", { name: /sample-kit/ })).toBeVisible();
  expect(screen.getByText(/Not installed in any workspace yet/)).toBeVisible();
  fireEvent.click(screen.getByText("Skills"));
  expect(screen.getByText("/review-diff")).toBeInTheDocument();
  expect(screen.getByText("Reviews the diff.")).toBeInTheDocument();
});

it("installs into chosen workspaces for every harness and names what a harness does not support", async () => {
  vi.mocked(api.kitsCatalog).mockResolvedValue({ ...catalog, kits: [uvSuite, sample] });
  vi.mocked(api.kitsPlan).mockResolvedValue({ inventory: sample.inventory!, plan });
  vi.mocked(api.kitsInstall).mockResolvedValue({ plan, report: { kit: "sample-kit", written: ["a"], removed: [], kept: [], notes: [] } });
  render(<SkillKitsPage onClose={() => {}} fallback={null} />);
  fireEvent.click((await screen.findAllByRole("button", { name: "Open" }))[1]);
  fireEvent.click(await screen.findByRole("button", { name: "Install into…" }));
  expect(screen.getByText(/no session has run here yet/)).toBeVisible();
  fireEvent.click(screen.getByLabelText("payments /ws/payments"));
  fireEvent.click(screen.getByRole("button", { name: "Review what will change" }));
  await waitFor(() => expect(api.kitsPlan).toHaveBeenCalledWith({ kit: "/kits/sample", project: "/ws/payments", harnesses: catalog.harnesses, options: {} }, ""));
  expect(await screen.findByText("opencode: 1 not supported")).toBeVisible();
  expect(screen.getByText(/1 soft · 0 hard/)).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: /Install into 1 repository/ }));
  await waitFor(() => expect(api.kitsInstall).toHaveBeenCalledWith({ kit: "/kits/sample", project: "/ws/payments", harnesses: catalog.harnesses, options: {}, replace: false }, ""));
  expect(await screen.findByRole("heading", { name: /sample-kit/ })).toBeVisible();
});

it("creates a team as a new workspace from the kit's installed repository", async () => {
  vi.mocked(api.kitsCatalog).mockResolvedValue(catalog);
  vi.mocked(api.kitsCreateTeam).mockResolvedValue({ team: { folder: "Payments squad", team: "uv-coding", description: "", kit: "uv-suite", kit_version: "0.34.0", project: "/ws/duckterm", roles: procedure.roles, rules: procedure.rules, created_at: 1 } });
  const created = vi.fn();
  render(<SkillKitsPage onClose={() => {}} fallback={null} onTeamCreated={created} />);
  fireEvent.click(await screen.findByRole("button", { name: "Create team" }));
  const name = screen.getByLabelText("Workspace name");
  expect(name).toHaveValue("uv-coding");
  fireEvent.change(name, { target: { value: "Payments squad" } });
  fireEvent.click(screen.getByRole("button", { name: "Create workspace" }));
  await waitFor(() => expect(api.kitsCreateTeam).toHaveBeenCalledWith({ project: "/ws/duckterm", kit: "uv-suite", team: "uv-coding", name: "Payments squad" }, ""));
  await waitFor(() => expect(created).toHaveBeenCalledWith("Payments squad"));
});

it("browses to a kit folder: plain folders cannot be picked, a multi-kit folder offers each kit", async () => {
  vi.mocked(api.kitsCatalog).mockResolvedValue(catalog);
  const downloads = { path: "/Users/me/Downloads", parent: "/Users/me", is_git: false, is_kit: false, kits: [], entries: [{ name: "uv-suite", path: "/Users/me/Downloads/uv-suite", is_git: false, is_kit: true }, { name: "photos", path: "/Users/me/Downloads/photos", is_git: false, is_kit: false }] };
  const uvSuiteFolder = { path: "/Users/me/Downloads/uv-suite", parent: "/Users/me/Downloads", is_git: false, is_kit: true, entries: [], kits: [
    { file: "skill-kit.toml", path: "/Users/me/Downloads/uv-suite/skill-kit.toml", name: "uv-suite", version: "0.34.0", description: "careful" },
    { file: "skill-kit.fast.toml", path: "/Users/me/Downloads/uv-suite/skill-kit.fast.toml", name: "uv-suite-fast", version: "0.34.0" },
  ] };
  vi.mocked(api.browse).mockImplementation(async (p?: string) => (p === "/Users/me/Downloads/uv-suite" ? uvSuiteFolder : downloads) as never);
  render(<SkillKitsPage onClose={() => {}} fallback={null} />);
  fireEvent.click(await screen.findByRole("button", { name: "Add skill kit…" }));
  fireEvent.click(screen.getByRole("button", { name: "Browse…" }));
  expect(await screen.findByText("uv-suite")).toBeVisible();
  expect(screen.getByText("skill kit")).toBeVisible();
  expect(screen.getByText(/Not a skill kit: no skill-kit.toml here/)).toBeVisible();
  expect(screen.getByRole("button", { name: "Use this kit folder" })).toBeDisabled();
  fireEvent.click(screen.getByText("uv-suite"));
  expect(await screen.findByText(/This folder holds 2 kits/)).toBeVisible();
  expect(screen.queryByRole("button", { name: "Use this kit folder" })).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Use uv-suite-fast" }));
  expect(screen.getByLabelText("Kit folder")).toHaveValue("/Users/me/Downloads/uv-suite/skill-kit.fast.toml");
});
