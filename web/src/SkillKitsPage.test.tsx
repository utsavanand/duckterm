import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { api, KitCatalog, KitEntry, KitPlan, KitReview } from "./api";
import { SkillKitsPage } from "./SkillKitsPage";

vi.mock("./api", () => ({
  api: { browse: vi.fn(), kitsCatalog: vi.fn(), kitsRegister: vi.fn(), kitsUnregister: vi.fn(), kitsPlan: vi.fn(), kitsReview: vi.fn(), kitsFix: vi.fn(), kitsEnable: vi.fn(), kitsDisable: vi.fn(), kitsUninstall: vi.fn(), kitsVerify: vi.fn(), kitsCreateTeam: vi.fn() },
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
  procedures: [procedure], flows: ["review"], runs: [], policy: { scope: "workspaces", workspaces: ["duckterm"] }, pending: [],
  installed_in: [{ workspace: "duckterm", project: "/ws/duckterm", version: "0.34.0", harnesses: ["claude-code", "codex"], installed_at: 1, trusted: true, verify: { ok: true, missing: [], drifted: [] } }],
};
const sample: KitEntry = {
  name: "sample-kit", path: "/kits/sample", version: "0.1.0", description: "One of every component kind",
  inventory: { name: "sample-kit", version: "0.1.0", description: "", root: "/kits/sample", options: {}, components: { skill: [{ name: "review-diff" }], hook: [{ name: "stop-remind-tests" }] }, component_count: 2, procedures: [], findings: [], valid: true },
  details: { skill: [{ name: "review-diff", description: "Reviews the diff." }] }, procedures: [], flows: [], runs: [], installed_in: [], policy: null, pending: [],
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

it("shows a kit whose folder changed since it was enabled as changed, not verified", async () => {
  vi.mocked(api.kitsCatalog).mockResolvedValue({ ...catalog, kits: [{ ...uvSuite, changed: true }] });
  render(<SkillKitsPage onClose={() => {}} fallback={<div>legacy</div>} />);
  expect(await screen.findByText("changed since enabled")).toBeVisible();
  expect(screen.queryByText("verified")).toBeNull();
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
  expect(screen.getByText(/Step 2 of 3/)).toBeVisible();
  fireEvent.click(screen.getByText("Skills"));
  expect(screen.getByText("/review-diff")).toBeInTheDocument();
  expect(screen.getByText("Reviews the diff.")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Next: choose workspaces" })).toBeEnabled();
  expect(screen.getByRole("button", { name: "Finish without enabling" })).toBeVisible();
});

const reviewOf = (over: Partial<KitReview> = {}): KitReview => ({
  inventory: sample.inventory!,
  plan: { ...plan, conflicts: [] },
  repositories: [{ workspace: "payments", project: "/ws/payments", installed: false, conflicts: [], codex_trusted: true }],
  pending: [""],
  fixes: [],
  ...over,
});

it("enables a kit for chosen workspaces: one matrix in sections, what each harness cannot take, and a Fix that reloads the review", async () => {
  vi.mocked(api.kitsCatalog).mockResolvedValue({ ...catalog, kits: [uvSuite, sample] });
  const knowledge = { component: "sample-kit/knowledge/api-conventions", kind: "knowledge", harnesses: { "claude-code": "installed", codex: "not supported", copilot: "installed", opencode: "not supported" }, notes: ["codex: codex: no path-scoped instructions (P10); Fix loads it always there", "opencode: opencode: no path-scoped instructions (P10); Fix loads it always there"] };
  const fixed = { ...knowledge, harnesses: { "claude-code": "installed", codex: "installed", copilot: "installed", opencode: "installed" }, notes: [] };
  vi.mocked(api.kitsReview)
    .mockResolvedValueOnce(reviewOf({ plan: { ...plan, conflicts: [], rows: [...plan.rows, knowledge] }, fixes: [{ kind: "fallback", component: knowledge.component, harnesses: ["codex", "opencode"] }] }))
    .mockResolvedValueOnce(reviewOf({ plan: { ...plan, conflicts: [], rows: [...plan.rows, fixed] } }));
  vi.mocked(api.kitsEnable).mockResolvedValue({ kit: "sample-kit", policy: { scope: "workspaces", workspaces: ["payments", ""] }, installed: [{ workspace: "payments", project: "/ws/payments" }], already: [], failed: [], pending: [""] });
  render(<SkillKitsPage onClose={() => {}} fallback={null} />);
  fireEvent.click((await screen.findAllByRole("button", { name: "Open" }))[1]);
  fireEvent.click(await screen.findByRole("button", { name: "Enable for workspaces…" }));
  fireEvent.click(screen.getByLabelText("These workspaces"));
  expect(screen.queryByText("/ws/payments")).toBeNull(); // workspaces only, no folders
  fireEvent.click(screen.getByLabelText("payments"));
  fireEvent.click(screen.getByLabelText("Ungrouped"));
  expect(screen.getByText("no repository yet")).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "Review what will change" }));
  await waitFor(() => expect(api.kitsReview).toHaveBeenCalledWith({ kit: "sample-kit", scope: "workspaces", workspaces: ["payments", ""], options: {}, fallbacks: [] }, ""));
  expect(await screen.findByText(/payments · every harness/)).toBeVisible();
  expect(screen.getByText("Knowledge")).toBeVisible();
  expect(screen.getByText("Hooks")).toBeVisible();
  expect(screen.getByText("opencode: 1 not supported")).toBeVisible();
  expect(screen.getByText("api-conventions: no path scope on codex, opencode")).toBeVisible();
  expect(screen.getAllByRole("heading", { name: /Review: sample-kit/ })).toHaveLength(1);
  fireEvent.click(screen.getByRole("button", { name: "Fix · load it always on codex, opencode" }));
  await waitFor(() => expect(api.kitsReview).toHaveBeenLastCalledWith({ kit: "sample-kit", scope: "workspaces", workspaces: ["payments", ""], options: {}, fallbacks: [knowledge.component] }, ""));
  await waitFor(() => expect(screen.queryByText("api-conventions: no path scope on codex, opencode")).toBeNull());
  fireEvent.click(screen.getByRole("button", { name: "Enable" }));
  await waitFor(() => expect(api.kitsEnable).toHaveBeenCalledWith({ kit: "sample-kit", scope: "workspaces", workspaces: ["payments", ""], options: {}, fallbacks: [knowledge.component], replace: false }, ""));
  expect(await screen.findByRole("heading", { name: "sample-kit is enabled" })).toBeVisible();
  expect(screen.getByText(/Waiting for a first session: Ungrouped/)).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "Done" }));
  expect(await screen.findByRole("heading", { name: "Skill kits" })).toBeVisible();
});

it("Codex trust and a conflicting file are fixed from the review's one strip", async () => {
  vi.mocked(api.kitsCatalog).mockResolvedValue({ ...catalog, kits: [uvSuite, sample] });
  const conflict = { path: ".claude/agents/reviewer.md", kind: "file-exists", detail: "exists and is not from a kit", choices: ["keep", "replace"], component: "sample-kit/agent/reviewer" };
  vi.mocked(api.kitsReview)
    .mockResolvedValueOnce(reviewOf({ repositories: [{ workspace: "payments", project: "/ws/payments", installed: false, conflicts: [conflict], codex_trusted: false }], fixes: [{ kind: "codex-trust", projects: ["/ws/payments"] }, { kind: "replace", project: "/ws/payments", path: conflict.path }] }))
    .mockResolvedValueOnce(reviewOf({ repositories: [{ workspace: "payments", project: "/ws/payments", installed: false, conflicts: [conflict], codex_trusted: true }], fixes: [{ kind: "replace", project: "/ws/payments", path: conflict.path }] }));
  vi.mocked(api.kitsFix).mockResolvedValue({ kind: "codex-trust", trusted: ["/ws/payments"] });
  vi.mocked(api.kitsEnable).mockResolvedValue({ kit: "sample-kit", policy: { scope: "workspaces", workspaces: ["payments"] }, installed: [{ workspace: "payments", project: "/ws/payments" }], already: [], failed: [], pending: [] });
  render(<SkillKitsPage onClose={() => {}} fallback={null} />);
  fireEvent.click((await screen.findAllByRole("button", { name: "Open" }))[1]);
  fireEvent.click(await screen.findByRole("button", { name: "Enable for workspaces…" }));
  fireEvent.click(screen.getByLabelText("These workspaces"));
  fireEvent.click(screen.getByLabelText("payments"));
  fireEvent.click(screen.getByRole("button", { name: "Review what will change" }));
  expect(await screen.findByText(/1 of 1 is not trusted yet/)).toBeVisible();
  expect(screen.getByText("codex: needs trust · fix above")).toBeVisible();
  expect(screen.getByText("2 can be fixed here")).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "Fix · trust it in Codex now" }));
  await waitFor(() => expect(api.kitsFix).toHaveBeenCalledWith({ kind: "codex-trust", projects: ["/ws/payments"] }, ""));
  await waitFor(() => expect(screen.queryByText(/not trusted yet/)).toBeNull());
  expect(screen.getByText("codex: needs trust")).toBeVisible();
  expect(screen.getByText(/it is kept and the component skipped there/)).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "Fix · replace with the kit's" }));
  expect(await screen.findByText(/it will be replaced with the kit's/)).toBeVisible();
  expect(screen.queryByText(/can be fixed here/)).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Enable" }));
  await waitFor(() => expect(api.kitsEnable).toHaveBeenCalledWith({ kit: "sample-kit", scope: "workspaces", workspaces: ["payments"], options: {}, fallbacks: [], replace: true }, ""));
});

it("enabling for all workspaces with no repository yet still reviews the kit's matrix, then keeps the choice", async () => {
  vi.mocked(api.kitsCatalog).mockResolvedValue({ ...catalog, workspaces: [], kits: [sample] });
  vi.mocked(api.kitsReview).mockResolvedValue(reviewOf({ repositories: [], pending: [] }));
  vi.mocked(api.kitsEnable).mockResolvedValue({ kit: "sample-kit", policy: { scope: "all" }, installed: [], already: [], failed: [], pending: [] });
  render(<SkillKitsPage onClose={() => {}} fallback={null} />);
  fireEvent.click(await screen.findByRole("button", { name: "Open" }));
  fireEvent.click(await screen.findByRole("button", { name: "Enable for workspaces…" }));
  expect(screen.getByLabelText("All workspaces")).toBeChecked();
  fireEvent.click(screen.getByRole("button", { name: "Review what will change" }));
  expect(await screen.findByText("Every workspace · every harness")).toBeVisible();
  expect(screen.getByText("Skills")).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "Enable" }));
  await waitFor(() => expect(api.kitsEnable).toHaveBeenCalledWith({ kit: "sample-kit", scope: "all", workspaces: undefined, options: {}, fallbacks: [], replace: false }, ""));
  expect(await screen.findByText(/Every workspace you create from now on gets the kit/)).toBeVisible();
});

it("a dry run the server refuses stays in the wizard, shows the reason, and lets the owner go back", async () => {
  vi.mocked(api.kitsCatalog).mockResolvedValue({ ...catalog, kits: [uvSuite, sample] });
  vi.mocked(api.kitsReview).mockRejectedValue(new Error("/ws/payments: no such directory: /ws/payments"));
  render(<SkillKitsPage onClose={() => {}} fallback={null} />);
  fireEvent.click((await screen.findAllByRole("button", { name: "Open" }))[1]);
  fireEvent.click(await screen.findByRole("button", { name: "Enable for workspaces…" }));
  fireEvent.click(screen.getByRole("button", { name: "Review what will change" }));
  const alert = await screen.findByRole("alert");
  expect(alert).toHaveTextContent("/ws/payments: no such directory: /ws/payments");
  expect(alert).toHaveTextContent("Nothing was written");
  expect(screen.getByLabelText("All workspaces")).toBeChecked();
  expect(screen.getByRole("button", { name: "Review what will change" })).toBeEnabled();
  expect(api.kitsEnable).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Back" }));
  expect(await screen.findByRole("heading", { name: /sample-kit/ })).toBeVisible();
  expect(screen.queryByRole("alert")).toBeNull();
});

it("a manifest error is shown once in the review and blocks Enable", async () => {
  vi.mocked(api.kitsCatalog).mockResolvedValue({ ...catalog, kits: [uvSuite, sample] });
  vi.mocked(api.kitsReview).mockResolvedValue(reviewOf({ plan: { ...plan, rows: [], files: [], conflicts: [], findings: [{ level: "error", component: "manifest", message: "a kit lists at least one component" }] }, repositories: [{ workspace: "duckterm", project: "/ws/duckterm", installed: false, conflicts: [], codex_trusted: null }, { workspace: "payments", project: "/ws/payments", installed: false, conflicts: [], codex_trusted: null }] }));
  render(<SkillKitsPage onClose={() => {}} fallback={null} />);
  fireEvent.click((await screen.findAllByRole("button", { name: "Open" }))[1]);
  fireEvent.click(await screen.findByRole("button", { name: "Enable for workspaces…" }));
  fireEvent.click(screen.getByRole("button", { name: "Review what will change" }));
  expect(await screen.findByText(/duckterm, payments · every harness/)).toBeVisible();
  expect(screen.getAllByText("manifest: a kit lists at least one component")).toHaveLength(1);
  expect(screen.getByText("1 error")).toBeVisible();
  expect(screen.getByRole("button", { name: "Enable" })).toBeDisabled();
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
  await waitFor(() => expect(api.kitsCreateTeam).toHaveBeenCalledWith({ project: "/ws/duckterm", kit_path: "/Users/me/ws-uv-suite", kit: "uv-suite", team: "uv-coding", name: "Payments squad" }, ""));
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
