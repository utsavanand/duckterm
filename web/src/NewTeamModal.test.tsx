import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { api, KitCatalog, KitEntry } from "./api";
import { NewTeamModal } from "./NewTeamModal";
import { ToastProvider } from "./ui";

vi.mock("./api", () => ({ api: { kitsCatalog: vi.fn(), kitsCreateTeam: vi.fn() } }));
afterEach(() => { cleanup(); vi.resetAllMocks(); });

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

it("lists every added MetaHarness's team and creates the chosen one through the card's request", async () => {
  vi.mocked(api.kitsCatalog).mockResolvedValue(catalog);
  vi.mocked(api.kitsCreateTeam).mockResolvedValue({ team: { folder: "Payments", team: "uv-coding", description: "", kit: "uv-suite", kit_version: "0.34.0", project: "/ws/duckterm", roles: coding.roles, rules: [], created_at: 1 } });
  const created = vi.fn();
  render(<NewTeamModal sessionKey="s1" existing={["duckterm"]} onClose={() => {}} onOpenKits={() => {}} onCreated={created} />);
  expect(await screen.findByLabelText("feature-squad from sample-kit")).not.toBeChecked();
  expect(screen.getByText("A reviewer beside a builder")).toBeVisible();
  expect(screen.getByText("dev · reviewer")).toBeVisible();
  expect(screen.getByRole("button", { name: "Create team" })).toBeDisabled(); // two teams: nothing chosen yet
  fireEvent.click(screen.getByLabelText("uv-coding from uv-suite"));
  const name = screen.getByLabelText("Workspace name");
  expect(name).toHaveValue("uv-coding");
  fireEvent.change(name, { target: { value: "Payments" } });
  fireEvent.click(screen.getByRole("button", { name: "Create team" }));
  // the installed repository wins over the added path, exactly as the kit card sends it
  await waitFor(() => expect(api.kitsCreateTeam).toHaveBeenCalledWith({ project: "/ws/duckterm", kit_path: "/kits/uv-suite", kit: "uv-suite", team: "uv-coding", name: "Payments" }, "s1"));
  await waitFor(() => expect(created).toHaveBeenCalledWith("Payments"));
});

it("preselects the only team and refuses a workspace name that exists", async () => {
  vi.mocked(api.kitsCatalog).mockResolvedValue({ ...catalog, kits: [sample] });
  render(<ToastProvider><NewTeamModal existing={["feature-squad"]} onClose={() => {}} onOpenKits={() => {}} onCreated={() => {}} /></ToastProvider>);
  expect(await screen.findByLabelText("feature-squad from sample-kit")).toBeChecked();
  expect(screen.getByLabelText("Workspace name")).toHaveValue("feature-squad");
  fireEvent.click(screen.getByRole("button", { name: "Create team" }));
  await waitFor(() => expect(screen.getByText('Workspace "feature-squad" already exists')).toBeVisible());
  expect(api.kitsCreateTeam).not.toHaveBeenCalled();
});

it("points at Settings › MetaHarnesses when no added MetaHarness offers a team", async () => {
  vi.mocked(api.kitsCatalog).mockResolvedValue({ ...catalog, kits: [{ ...sample, procedures: [] }] });
  const openKits = vi.fn();
  render(<NewTeamModal existing={[]} onClose={() => {}} onOpenKits={openKits} onCreated={() => {}} />);
  expect(await screen.findByText(/None of the added MetaHarnesses offers a team/)).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "Open Settings › MetaHarnesses" }));
  expect(openKits).toHaveBeenCalledTimes(1);
});
