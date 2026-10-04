import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { UpdateDuckTerm, UpdateStatus } from "./UpdateDuckTerm";
const state: UpdateStatus = { installed_version: "0.4.96", latest_version: "0.4.97", release_url: "https://github.com/utsavanand/duckterm/releases/tag/v0.4.97", update_available: true, install_available: false, reason: "In-app installation is not available yet.", check_status: "checked", check_error: null, operation: null };
afterEach(() => { vi.unstubAllGlobals(); vi.useRealTimers(); });
it("checks on open and explicit retry only, and never enables installation", async () => {
  const fetcher = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ ...state, install_available: true }) }); vi.stubGlobal("fetch", fetcher);
  const view = render(<UpdateDuckTerm />); await screen.findByText("0.4.97 available");
  const install = screen.getByRole("button", { name: "Install update" }); expect(install).toBeDisabled(); fireEvent.click(install);
  vi.useFakeTimers(); act(() => vi.advanceTimersByTime(60000)); expect(fetcher).toHaveBeenCalledTimes(1); vi.useRealTimers();
  fireEvent.click(screen.getByRole("button", { name: "Check again" })); await screen.findByText("0.4.97 available"); expect(fetcher).toHaveBeenCalledTimes(2);
  view.unmount(); render(<UpdateDuckTerm />); await screen.findByText("0.4.97 available"); expect(fetcher).toHaveBeenCalledTimes(3);
  for (const [, init] of fetcher.mock.calls) expect(init.method).toBeUndefined();
});
it("removes stale release information when the next check fails", async () => {
  const fetcher = vi.fn().mockResolvedValueOnce({ ok: true, json: async () => state }).mockResolvedValue({ ok: true, json: async () => ({ ...state, latest_version: null, check_status: "failed", check_error: "Offline", update_available: null }) }); vi.stubGlobal("fetch", fetcher);
  render(<UpdateDuckTerm />); await screen.findByText("0.4.97 available"); fireEvent.click(screen.getByRole("button", { name: "Check again" }));
  await screen.findByRole("alert"); expect(screen.queryByText("0.4.97 available")).toBeNull(); expect(screen.queryByText("Up to date")).toBeNull();
});
it("requires verified installed version before claiming success and preserves rollback errors", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, json: async () => ({ ...state, operation: { target_version: "0.4.97", outcome: "succeeded", phase: "verify", verified: false, rollback: "failed", error: "Could not restore snapshot" } }) }));
  render(<UpdateDuckTerm />); await screen.findByText("Awaiting version verification"); expect(screen.queryByText("Updated to 0.4.97")).toBeNull(); expect(screen.getByText("Rollback: failed")).toBeVisible(); expect(screen.getByText("Could not restore snapshot")).toBeVisible();
});
