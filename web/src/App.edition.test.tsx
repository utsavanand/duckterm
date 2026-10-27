import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { App } from "./App";
import { SessionView, viewFromPersisted } from "./types";
import { CODE, FULL, LITE } from "./test/editions";

const probe = vi.hoisted(() => ({
  connected: true, sessions: [] as SessionView[], tree: {} as Record<string, unknown>, edition: null as unknown, requests: [] as string[],
}));
vi.mock("./useEventStream", () => ({ useEventStream: () => ({ sessions: probe.sessions, connected: probe.connected, loadedHosts: ["local"], removeSessions: vi.fn(), patchSession: vi.fn() }) }));
vi.mock("./useFolders", () => ({ useFolders: () => ({ folders: ["Team"], refreshFolders: vi.fn() }) }));
vi.mock("./Terminal", () => ({ Terminal: () => <input aria-label="Terminal input" /> }));
vi.mock("./SessionShell", () => ({ SessionShell: () => null }));
vi.mock("./Connectors", () => ({ Connectors: () => null }));
vi.mock("./ContextPanel", () => ({ ContextPanel: () => null }));
vi.mock("./AgentTree", () => ({ AgentTree: (props: Record<string, unknown>) => { probe.tree = props; return null; } }));

// Requests a Lite dashboard must never make, by endpoint family.
const ADVANCED = [/^\/relay/, /^\/session-inbox-counts/, /^\/agents-md/, /\/checkpoints/, /\/artifacts/, /\/prs/, /^\/connectors/, /^\/oracle/, /^\/layouts/, /^\/tasks/];

beforeEach(() => {
  probe.requests = []; probe.connected = true;
  probe.sessions = [{ ...viewFromPersisted({ session_key: "one", name: "one", state: "idle", started_at: 1, updated_at: 1, event_count: 1, cwd: "/work" } as never), ptyOwned: true }];
  vi.stubGlobal("ResizeObserver", class { observe() {} disconnect() {} });
  vi.stubGlobal("matchMedia", () => ({ matches: false, addEventListener() {}, removeEventListener() {} }));
  vi.stubGlobal("fetch", vi.fn(async (input: string) => {
    const path = String(input);
    probe.requests.push(path);
    const body = path === "/edition" ? probe.edition : path.startsWith("/session-inbox-counts") ? { counts: {} } : path.startsWith("/relay/count") ? { open: 0 } : { pins: [], open: false, requests: [] };
    return new Response(JSON.stringify(body ?? { error: "Unavailable" }), { status: body ? 200 : 503 });
  }));
});
afterEach(() => { cleanup(); vi.useRealTimers(); vi.unstubAllGlobals(); localStorage.clear(); });

async function mount(features: string[] | null, id: "lite" | "code" | "full" = "full") {
  probe.edition = features && { id, features };
  render(<App />);
  await act(async () => { await new Promise(resolve => setTimeout(resolve, 20)); });
}
const tab = (name: string) => screen.queryByRole("button", { name: new RegExp(`^${name}`) });

it("Lite shows the core session views and makes no advanced requests", async () => {
  await mount(LITE, "lite");
  await act(async () => { await new Promise(resolve => setTimeout(resolve, 3100)); });
  for (const name of ["Terminal", "Messages", "AGENTS.md"]) expect(tab(name)).toBeInTheDocument();
  for (const name of ["Timeline", "Inbox", "Artifacts", "Explorer", "PRs", "Oracle"]) expect(tab(name)).not.toBeInTheDocument();
  expect(screen.queryByRole("tab", { name: "Connectors" })).not.toBeInTheDocument();
  expect(probe.tree.onOpenInbox).toBeUndefined();
  expect(probe.tree.onOpenFolder).toBeUndefined();
  expect(probe.tree.onOpenFolderInbox).toBeUndefined();
  expect(probe.requests).toContain("/edition");
  expect(probe.requests.filter(path => ADVANCED.some(pattern => pattern.test(path)))).toEqual([]);
}, 10_000);

it("Code has the Explorer and pull requests, but no Oracle or artifacts", async () => {
  await mount(CODE, "code");
  for (const name of ["Terminal", "Messages", "Timeline", "Inbox", "Explorer", "PRs", "AGENTS.md"]) expect(tab(name)).toBeInTheDocument();
  for (const name of ["Artifacts", "Oracle"]) expect(tab(name)).not.toBeInTheDocument();
  expect(screen.getByRole("tab", { name: "Connectors" })).toBeInTheDocument();
  expect(probe.requests.filter(path => /^\/relay|\/artifacts/.test(path))).toEqual([]);
});

it("Full keeps today's views except pull request review", async () => {
  await mount(FULL);
  for (const name of ["Terminal", "Messages", "Timeline", "Inbox", "Artifacts", "Oracle", "AGENTS.md"]) expect(tab(name)).toBeInTheDocument();
  expect(tab("PRs")).not.toBeInTheDocument();
  expect(probe.requests).toContain("/relay/count");
});

it("keeps only the core when the edition is unavailable, rather than assuming Full", async () => {
  await mount(null);
  expect(probe.requests).toContain("/edition");
  expect(tab("Terminal")).toBeInTheDocument();
  for (const name of ["Timeline", "Inbox", "Artifacts", "Oracle"]) expect(tab(name)).not.toBeInTheDocument();
  expect(await screen.findByText(/Feature list unavailable/)).toBeInTheDocument();
  expect(probe.requests.filter(path => ADVANCED.some(pattern => pattern.test(path)))).toEqual([]);
});

it("returns to a remembered advanced view when the edition provides it again", async () => {
  localStorage.setItem("rd.contextView", "connectors");
  await mount(LITE, "lite");
  expect(screen.getByRole("tab", { name: "Session" })).toHaveAttribute("aria-selected", "true");
  cleanup();
  await mount(CODE, "code");
  expect(screen.getByRole("tab", { name: "Connectors" })).toHaveAttribute("aria-selected", "true");
});

it("rereads the edition when the server connection comes back", async () => {
  await mount(FULL);
  expect(tab("Oracle")).toBeInTheDocument();
  probe.connected = false;
  render(<App />, { container: document.body.firstElementChild as HTMLElement });
  probe.edition = { id: "code", features: CODE };
  probe.connected = true;
  render(<App />, { container: document.body.firstElementChild as HTMLElement });
  await act(async () => { await new Promise(resolve => setTimeout(resolve, 20)); });
  expect(tab("Oracle")).not.toBeInTheDocument();
  expect(tab("Explorer")).toBeInTheDocument();
});
