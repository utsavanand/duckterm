import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { HeaderMenus } from "./HeaderMenus";
import { StaticEdition } from "./edition";
import { FeatureFlagsModal } from "./FeatureFlagsModal";
import { api } from "./api";
vi.mock("./UpdateDuckTerm", () => ({ UpdateDuckTerm: () => null }));
vi.mock("./api", () => ({ api: { featureFlags: vi.fn() } }));
afterEach(() => { cleanup(); vi.resetAllMocks(); });
const core = ["inbox", "checkpoints", "handoff", "context"];
const flags = ["cross_computer_collaboration", "skill_kits", "harness_management", "backup", "byoc", "byok"];
function menu(features: string[]) {
  render(<StaticEdition features={features}><HeaderMenus density="standard" onDensity={() => {}} notifyOn={false} onNotify={() => {}} onAction={() => {}} /></StaticEdition>);
  fireEvent.click(screen.getByRole("button", { name: /Settings/ }));
}
it("keeps core settings but removes gated actions from distributed apps", () => {
  menu(core);
  for (const name of [/Collaboration/, /Harnesses/, /Agent setup/, /Back up to remote/]) expect(screen.queryByRole("button", { name })).toBeNull();
  for (const name of [/Checkpointing/, /AI models/, /Feature flags/]) expect(screen.getByRole("button", { name })).toBeVisible();
});
it("shows the gated settings on an opted-in personal installation", () => {
  menu([...core, ...flags]);
  for (const name of [/Collaboration/, /Harnesses/, /Agent setup/, /Back up to remote/]) expect(screen.getByRole("button", { name })).toBeVisible();
});
it("shows the server's effective flags without editable switches", async () => {
  vi.mocked(api.featureFlags).mockResolvedValue({ restart_required: true, flags: [{id:"backup", name:"Backup", default:false, enabled:true, reason:"Local override"}] });
  render(<FeatureFlagsModal onClose={() => {}} />);
  expect(await screen.findByText("Local override")).toBeVisible();
  expect(screen.getByText("Off")).toBeVisible(); expect(screen.getByText("On")).toBeVisible();
  expect(screen.queryByRole("checkbox")).toBeNull();
});
