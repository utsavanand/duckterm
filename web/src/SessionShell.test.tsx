import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { SessionShell } from "./SessionShell";
import { routedFetch, sessionRef } from "./hostTransport";
import type { SessionView } from "./types";
vi.mock("./Terminal", () => ({ Terminal: () => <div>Shell terminal</div> }));
vi.mock("./hostTransport", async importOriginal => ({ ...await importOriginal<typeof import("./hostTransport")>(), routedFetch: vi.fn() }));
const request = vi.mocked(routedFetch);
const session = { key: "shell-test", label: "Test agent", state: "idle", ptyOwned: true, cwd: "/tmp" } as SessionView;
const state = (token: string, foreground: string) => ({ open: true, pane_id: "%4", confirmation_required: true, confirmation_token: token, foreground });
const response = (body: object, status = 200) => Promise.resolve(new Response(JSON.stringify(body), { status }));
beforeEach(() => {
  localStorage.clear(); request.mockReset();
  document.body.innerHTML = '<span id="rd-shell-toggle"></span>';
  vi.stubGlobal("ResizeObserver", class { observe() {} disconnect() {} });
  HTMLDialogElement.prototype.showModal = function () { this.open = true; };
  HTMLDialogElement.prototype.close = function () { this.open = false; };
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });
it("requires a new owner click when the process changed after confirmation", async () => {
  request.mockImplementation((_path, init) => {
    if (init?.method === "DELETE") {
      const body = JSON.parse(String(init.body));
      return body.confirmation_token === "new" ? response({ open: false }) : response(state(body.force ? "new" : "old", body.force ? "python" : "sleep"), 409);
    }
    return response(state("old", "sleep"));
  });
  render(<SessionShell session={session} active theme="dark" />);
  await screen.findByRole("button", { name: "Close collapsed shell" });
  fireEvent.click(screen.getByRole("button", { name: "Close collapsed shell" }));
  await screen.findByText("sleep", { selector: "code" });
  fireEvent.click(screen.getByRole("button", { name: "Close shell" }));
  await screen.findByText("The running process changed. Review it before closing.");
  expect(screen.getByText("python", { selector: "code" })).toBeTruthy();
  expect(request.mock.calls.filter(([, init]) => init?.method === "DELETE")).toHaveLength(2);
  fireEvent.click(screen.getByRole("button", { name: "Close shell" }));
  await waitFor(() => expect(screen.queryByRole("button", { name: "Close collapsed shell" })).toBeNull());
  const bodies = request.mock.calls.filter(([, init]) => init?.method === "DELETE").map(([, init]) => JSON.parse(String(init?.body)));
  expect(bodies).toEqual([{}, { force: true, confirmation_token: "old" }, { force: true, confirmation_token: "new" }]);
});
it("does not create a shell during refresh or use local fallback for an offline remote", async () => {
  request.mockImplementation(() => response({ open: false }));
  const view = render(<SessionShell session={session} active theme="dark" />);
  await waitFor(() => expect(request).toHaveBeenCalledOnce());
  expect(request.mock.calls[0][1]?.method).toBeUndefined();
  view.unmount(); request.mockClear();
  render(<SessionShell session={{ ...session, key: sessionRef("remote", "shell-test"), hostOffline: true }} active theme="dark" />);
  fireEvent.click(screen.getByRole("button", { name: "›_ Shell" }));
  await screen.findByText("Shell unavailable", { exact: true });
  expect(request).not.toHaveBeenCalled();
});
