import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { api, KitCatalog, KitEntry } from "./api";
import { NewTeamModal } from "./NewTeamModal";
import { ToastProvider } from "./ui";

vi.mock("./api", () => ({ api: { kitsCatalog: vi.fn(), kitsCreateTeam: vi.fn(), harnessPreferences: vi.fn(), browse: vi.fn() } }));
afterEach(() => { cleanup(); vi.resetAllMocks(); });
beforeEach(() => {
  vi.mocked(api.harnessPreferences).mockResolvedValue({ default_harness: null, present: ["claude-code", "codex"], effective: "claude-code" });
});

const squad = {
  name: "feature-squad", description: "A reviewer beside a builder", template: "teams/feature-squad.toml",
  roles: { dev: { purpose: "Builds", skills: ["raise-pr"] }, reviewer: { purpose: "Reviews", skills: ["review-diff"] } },
  rules: [],
};
const coding = { name: "uv-coding", description: "", template: "teams/uv-coding.toml", roles: { architect: { purpose: "", skills: [] } }, rules: [] };
const sample: KitEntry = { name: "sample-kit", path: "/kits/sample-kit", installed_in: [], procedures: [squad] };
const uvSuite: KitEntry = {
  name: "uv-suite", path: "/kits/uv-suite", procedures: [coding],
  installed_in: [{ workspace: "duckterm", project: "/ws/duckterm", version: "0.34.0", harnesses: ["claude-code"], installed_at: 1, trusted: true, verify: { ok: true, missing: [], drifted: [] } }],
};
const catalog: KitCatalog = { kits: [sample, uvSuite], workspaces: [], harnesses: ["claude-code"], harnesses_present: { "claude-code": true } };
const created = (folder: string, team: string, roles: Record<string, { purpose: string; skills: string[] }>) =>
  ({ team: { folder, team, description: "", kit: "uv-suite", kit_version: "0.34.0", project: "/ws/duckterm", roles, rules: [], created_at: 1, harness: "claude-code", sessions: Object.keys(roles).map((role) => ({ role, session_key: `k-${role}` })) }, sessions: Object.keys(roles).map((role) => ({ role, session_key: `k-${role}` })), failed: [], harness: "claude-code" });

it("lists every added MetaHarness's team, takes the installed repository as the folder and starts a session per role", async () => {
  vi.mocked(api.kitsCatalog).mockResolvedValue(catalog);
  vi.mocked(api.kitsCreateTeam).mockResolvedValue(created("Payments", "uv-coding", coding.roles));
  const done = vi.fn();
  render(<NewTeamModal sessionKey="s1" existing={["duckterm"]} onClose={() => {}} onOpenKits={() => {}} onCreated={done} />);
  expect(await screen.findByLabelText("feature-squad from sample-kit")).not.toBeChecked();
  expect(screen.getByText("A reviewer beside a builder")).toBeVisible();
  expect(screen.getByText("dev · reviewer")).toBeVisible();
  expect(screen.getByRole("button", { name: "Create team" })).toBeDisabled(); // two teams: nothing chosen yet
  fireEvent.click(screen.getByLabelText("uv-coding from uv-suite"));
  const name = screen.getByLabelText("Workspace name");
  expect(name).toHaveValue("uv-coding");
  expect(screen.getByLabelText("Folder the team works in")).toHaveValue("/ws/duckterm");
  expect(await screen.findByText(/Start one session per role now/)).toHaveTextContent("Start one session per role now, on claude-code (automatic; the default harness is in Settings)");
  fireEvent.change(name, { target: { value: "Payments" } });
  fireEvent.click(screen.getByRole("button", { name: "Create team · 1 session" }));
  await waitFor(() => expect(api.kitsCreateTeam).toHaveBeenCalledWith({ project: "/ws/duckterm", kit_path: "/kits/uv-suite", kit: "uv-suite", team: "uv-coding", name: "Payments", sessions: true }, "s1"));
  await waitFor(() => expect(done).toHaveBeenCalledWith("Payments"));
});

it("preselects the only team, needs a folder, and refuses a workspace name that exists", async () => {
  vi.mocked(api.kitsCatalog).mockResolvedValue({ ...catalog, kits: [sample] });
  render(<ToastProvider><NewTeamModal existing={["feature-squad"]} onClose={() => {}} onOpenKits={() => {}} onCreated={() => {}} /></ToastProvider>);
  expect(await screen.findByLabelText("feature-squad from sample-kit")).toBeChecked();
  expect(screen.getByLabelText("Workspace name")).toHaveValue("feature-squad");
  const button = await screen.findByRole("button", { name: "Create team · 2 sessions" });
  expect(button).toBeDisabled(); // the kit is installed nowhere, so no folder is known yet
  fireEvent.change(screen.getByLabelText("Folder the team works in"), { target: { value: "/repos/payments" } });
  expect(button).toBeEnabled();
  fireEvent.click(button);
  await waitFor(() => expect(screen.getByText('Workspace "feature-squad" already exists')).toBeVisible());
  expect(api.kitsCreateTeam).not.toHaveBeenCalled();
});

it("creates the workspace without sessions when no coding harness is installed, or when told not to", async () => {
  vi.mocked(api.harnessPreferences).mockResolvedValue({ default_harness: null, present: [], effective: null });
  vi.mocked(api.kitsCatalog).mockResolvedValue({ ...catalog, kits: [uvSuite] });
  vi.mocked(api.kitsCreateTeam).mockResolvedValue({ ...created("uv-coding", "uv-coding", coding.roles), sessions: [], harness: null });
  render(<NewTeamModal existing={[]} onClose={() => {}} onOpenKits={() => {}} onCreated={() => {}} />);
  expect(await screen.findByText(/No coding harness is installed/)).toBeVisible();
  expect(screen.getByRole("checkbox")).toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: "Create team" }));
  await waitFor(() => expect(api.kitsCreateTeam).toHaveBeenCalledWith(expect.objectContaining({ project: "/ws/duckterm", sessions: false }), undefined));
});

it("points at Settings › MetaHarnesses when no added MetaHarness offers a team", async () => {
  vi.mocked(api.kitsCatalog).mockResolvedValue({ ...catalog, kits: [{ ...sample, procedures: [] }] });
  const openKits = vi.fn();
  render(<NewTeamModal existing={[]} onClose={() => {}} onOpenKits={openKits} onCreated={() => {}} />);
  expect(await screen.findByText(/None of the added MetaHarnesses offers a team/)).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "Open Settings › MetaHarnesses" }));
  expect(openKits).toHaveBeenCalledTimes(1);
});
