import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { UpdateDuckTerm } from "./UpdateDuckTerm";
import type { AppUpdateState } from "./NativeAppUpdate";
const available: AppUpdateState = { phase: "available", installed_version: "0.4.208", latest_version: "0.4.210", can_cancel: true };
function bridge(state: AppUpdateState) {
  const postMessage = vi.fn().mockResolvedValue(state);
  window.__rubbertermDesktop = { currentTarget: "local", canUpdateApp: true, targets: [] };
  window.webkit = { messageHandlers: { appUpdate: { postMessage } } };
  return postMessage;
}
afterEach(() => { cleanup(); delete window.webkit; delete window.__rubbertermDesktop; vi.unstubAllGlobals(); });
it("uses the native installer only after Update & restart, never a server install endpoint", async () => {
  const native = bridge(available), fetcher = vi.fn(); vi.stubGlobal("fetch", fetcher);
  render(<UpdateDuckTerm />); await screen.findByText("0.4.210 available");
  expect(native.mock.calls).toEqual([[{ operation: "status" }]]);
  fireEvent.click(screen.getByRole("button", { name: "Update & restart" }));
  await waitFor(() => expect(native).toHaveBeenCalledWith({ operation: "install" }));
  expect(fetcher).not.toHaveBeenCalled(); expect(screen.queryByRole("link", { name: /Download/ })).toBeNull();
});
it("shows native download progress, cancellation, and preserves progress when reopened", async () => {
  const native = bridge({ ...available, phase: "downloading", progress: 0.42 });
  const view = render(<UpdateDuckTerm />); await screen.findByText(/42%/);
  expect(screen.getByRole("progressbar")).toHaveAttribute("value", "0.42");
  view.unmount(); render(<UpdateDuckTerm />); await screen.findByText(/42%/);
  expect(native).not.toHaveBeenCalledWith({ operation: "check" });
  fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
  await waitFor(() => expect(native).toHaveBeenCalledWith({ operation: "cancel" }));
});
it("waits for native verification before showing Updated and displays failures", async () => {
  bridge({ ...available, phase: "restarting", can_cancel: false });
  render(<UpdateDuckTerm />); await screen.findByText("Restarting DuckTerm…");
  expect(screen.queryByText(/Updated to/)).toBeNull();
  expect(screen.queryByRole("button", { name: "Cancel" })).toBeNull();
  act(() => window.dispatchEvent(new CustomEvent("app-update", { detail: { ...available, phase: "failed", error: "Backend version did not match" } })));
  expect(await screen.findByRole("alert")).toHaveTextContent("Backend version did not match");
  expect(screen.getByRole("button", { name: "Retry" })).toBeVisible();
});
it("does not expose a local native install action to a remote dashboard", async () => {
  const native = bridge(available); window.__rubbertermDesktop!.currentTarget = "remote";
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, json: async () => ({ installed_version: "0.4.208", release_url: "", check_status: "checked", operation: null }) }));
  render(<UpdateDuckTerm />); await screen.findByText("Installed 0.4.208");
  expect(screen.queryByRole("button", { name: "Update & restart" })).toBeNull(); expect(native).not.toHaveBeenCalled();
});
