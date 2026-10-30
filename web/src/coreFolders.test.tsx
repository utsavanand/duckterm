import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { api } from "./api";
import { EditionProvider, useEdition } from "./edition";

function Status() {
  const { state, has } = useEdition();
  return <><span>{state.status}</span><output>collaboration:{String(has("inbox"))}</output></>;
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  delete window.__rubbertermDesktop;
  delete window.webkit;
});

function nativeBridge() {
  const request = vi.fn().mockRejectedValue(new Error("This action is unavailable in Duckterm Lite"));
  window.__rubbertermDesktop = { currentTarget: "local", canCollaborate: true, targets: [{ id: "local", name: "This Mac" }] };
  window.webkit = { messageHandlers: { launchRequest: { postMessage: request } } };
  return request;
}

it("uses core HTTP folder and session operations in Lite despite the native collaboration bridge", async () => {
  const native = nativeBridge();
  const http = vi.fn(async (path: string) => new Response(JSON.stringify(path === "/edition"
    ? { id: "lite", features: ["folders", "terminal"] } : { created: "Project", moved: "Project", to: "Team/Project", deleted: "Project", updated: true })));
  vi.stubGlobal("fetch", http);
  render(<EditionProvider><Status /></EditionProvider>);
  await screen.findByText("ready");
  await api.createFolder("Project");
  await api.moveFolder("Project", "Team");
  await api.renameFolder("Team/Project", "Renamed");
  await api.setGroup("local-session", "Team/Renamed");
  await api.deleteFolder("Team/Renamed");
  expect(native).not.toHaveBeenCalled();
  expect(http.mock.calls.map(call => call[0])).toEqual([
    "/edition", "/folders", "/folders/Project", "/folders/Team%2FProject", "/sessions/local-session", "/folders/Team%2FRenamed",
  ]);
});

it.each(["code", "full"])("preserves coordinated folder actions and pending results in %s", async id => {
  const native = nativeBridge().mockResolvedValue({ handled: true, pending: true });
  const http = vi.fn().mockResolvedValue(new Response(JSON.stringify({ id, features: ["folders", "inbox"] })));
  vi.stubGlobal("fetch", http);
  render(<EditionProvider><Status /></EditionProvider>);
  await screen.findByText("ready");
  expect(await api.createFolder("Shared")).toMatchObject({ pending: true, created: "Shared" });
  await api.setGroup("local-session", "Shared");
  expect(native.mock.calls).toEqual([
    [{ target: "local", operation: "collaboration-folder", params: { action: "create", old: "", new: "Shared" } }],
    [{ target: "local", operation: "collaboration-move", params: { key: "local-session", folder: "Shared" } }],
  ]);
  expect(http).toHaveBeenCalledTimes(1);
});

it("keeps core folder creation available while edition loading fails, then follows recovery and revocation", async () => {
  const native = nativeBridge().mockResolvedValue({ handled: true, pending: false });
  let description: object | null = null;
  let finish: ((response: Response) => void) | undefined;
  const http = vi.fn((path: string) => {
    if (path !== "/edition") return Promise.resolve(new Response('{"created":"Local"}'));
    if (!description) return new Promise<Response>(resolve => { finish = resolve; });
    return Promise.resolve(new Response(JSON.stringify(description)));
  });
  vi.stubGlobal("fetch", http);
  render(<EditionProvider><Status /></EditionProvider>);
  await api.createFolder("Local");
  expect(native).not.toHaveBeenCalled();
  await act(async () => finish!(new Response('{"error":"Unavailable"}', { status: 503 })));
  await screen.findByText("error");
  await api.createFolder("Local");
  expect(native).not.toHaveBeenCalled();
  description = { id: "full", features: ["folders", "inbox"] };
  act(() => window.dispatchEvent(new Event("focus")));
  await screen.findByText("ready");
  await api.createFolder("Shared");
  expect(native).toHaveBeenCalledTimes(1);
  description = { id: "full", features: ["folders"] };
  act(() => window.dispatchEvent(new Event("focus")));
  await screen.findByText("collaboration:false");
  await api.createFolder("Local");
  expect(native).toHaveBeenCalledTimes(1);
});
