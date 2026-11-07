import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
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

const ADVANCED = [/^\/relay/, /^\/session-inbox-counts/, /^\/agents-md/, /\/checkpoints/, /\/artifacts/, /\/prs/, /^\/connectors/, /^\/oracle/, /^\/layouts/, /^\/tasks/, /^\/control-tower/, /\/files\b/];
const LITE_UNAVAILABLE = [/^\/relay/, /^\/oracle/, /^\/agents-md/, /\/prs\b/, /^\/connectors/, /\/files\b/, /\/file-content\b/, /\/folders\/.+\/chat/, /^\/fleet\/ask/];

beforeEach(() => {
  probe.requests = []; probe.connected = true;
  probe.sessions = [{ ...viewFromPersisted({ session_key: "one", name: "one", state: "idle", started_at: 1, updated_at: 1, event_count: 1, cwd: "/work" } as never), ptyOwned: true }];
  vi.stubGlobal("ResizeObserver", class { observe() {} disconnect() {} });
  vi.stubGlobal("matchMedia", () => ({ matches: false, addEventListener() {}, removeEventListener() {} }));
  vi.stubGlobal("fetch", vi.fn(async (input: string) => {
    const path = String(input);
    probe.requests.push(path);
    const body = path === "/edition" ? probe.edition
      : path.startsWith("/session-inbox-counts") ? { counts: {} }
      : path === "/relay/count" ? { open: 0 }
      : path === "/relay" ? { notes: [], rules: [], open: 0 }
      : path === "/control-tower" ? { tokens: { days: 7, by_agent: {} }, mail: { sent: 0, answered: 0, nudges: 0 }, backup: {}, remote: { available: false, count: 0 } }
      : path.startsWith("/layouts/") ? { revision: "default", instances: [] }
      : path === "/tasks" ? { tasks: [] }
      : path.includes("/files?") ? { available: true, root: "/work", path: ".", entries: [], artifacts: [], next_cursor: null }
      : path.endsWith("/artifacts") ? { artifacts: [] }
      : path.endsWith("/stats") ? { folder: "Team", updated_at: 1, sessions: { busy: 0, idle: 1, waiting: 0 }, waiting: [], periods: { "1": { tokens: 0, sent: 0, answered: 0 } }, artifacts: { by_kind: {}, available: 0, removed: 0 } }
      : path.includes("/folder-interactions") || path.endsWith("/chat") ? { messages: [], next_cursor: null }
      : { pins: [], open: false, requests: [] };
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

it("Lite exposes continuity, inbox, artifacts and folder overview without coding or Oracle requests", async () => {
  await mount(LITE, "lite");
  await act(async () => { await new Promise(resolve => setTimeout(resolve, 3100)); });
  for (const name of ["Terminal", "Messages", "Timeline", "Inbox", "Artifacts", "Overview", "AGENTS.md"]) expect(tab(name)).toBeInTheDocument();
  for (const name of ["Explorer", "PRs", "Oracle"]) expect(tab(name)).not.toBeInTheDocument();
  expect(screen.queryByRole("tab", { name: "Connectors" })).not.toBeInTheDocument();
  expect(probe.tree.onOpenInbox).toBeTypeOf("function");
  expect(probe.tree.onOpenFolder).toBeTypeOf("function");
  expect(probe.tree.onOpenFolderInbox).toBeTypeOf("function");
  expect(probe.requests).toContain("/edition");
  expect(probe.requests).toContain("/session-inbox-counts");
  await act(async () => { fireEvent.click(tab("Artifacts")!); });
  expect(await screen.findByRole("region", { name: "Session artifacts" })).toBeVisible();
  expect(screen.queryByRole("region", { name: "Session files" })).toBeNull();
  expect(probe.requests).toContain("/sessions/one/artifacts");
  await act(async () => { (probe.tree.onOpenFolder as (folder: string) => void)("Team"); });
  expect(screen.getByRole("tab", { name: "Overview" })).toHaveAttribute("aria-selected", "true");
  expect(screen.queryByRole("tab", { name: "Chat" })).toBeNull();
  expect(screen.getByRole("complementary", { name: "Folder overview" })).toBeVisible();
  expect(probe.requests.filter(path => LITE_UNAVAILABLE.some(pattern => pattern.test(path)))).toEqual([]);
}, 10_000);

it("Code combines Explorer files and saved artifacts, with pull requests and no Oracle", async () => {
  await mount(CODE, "code");
  for (const name of ["Terminal", "Messages", "Timeline", "Inbox", "Artifacts", "Overview", "PRs", "AGENTS.md"]) expect(tab(name)).toBeInTheDocument();
  expect(tab("Oracle")).not.toBeInTheDocument();
  expect(screen.getByRole("tab", { name: "Connectors" })).toBeInTheDocument();
  await act(async () => { fireEvent.click(tab("Artifacts")!); });
  expect(await screen.findByRole("region", { name: "Session files" })).toBeVisible();
  expect(screen.getByRole("group", { name: "Files filter" })).toBeVisible();
  expect(probe.requests.some(path => path.startsWith("/sessions/one/files?"))).toBe(true);
  expect(probe.requests.filter(path => /^\/relay|^\/oracle/.test(path))).toEqual([]);
});

it("Full keeps every view, including pull request review", async () => {
  await mount(FULL);
  for (const name of ["Terminal", "Messages", "Timeline", "Inbox", "Artifacts", "Oracle", "AGENTS.md", "PRs"]) expect(tab(name)).toBeInTheDocument();
  expect(probe.requests).toContain("/relay/count");
});

it("keeps only the core when the edition is unavailable, rather than assuming Full", async () => {
  await mount(null);
  expect(probe.requests).toContain("/edition");
  expect(tab("Terminal")).toBeInTheDocument();
  for (const name of ["Timeline", "Inbox", "Artifacts", "Oracle", "Overview"]) expect(tab(name)).not.toBeInTheDocument();
  expect(probe.tree.onOpenFolder).toBeUndefined();
  expect(probe.tree.onOpenFolderInbox).toBeUndefined();
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
  expect(tab("Artifacts")).toBeInTheDocument();
  expect(tab("Overview")).toBeInTheDocument();
});

it.each([["lite", LITE], ["code", CODE]] as const)("%s Overview preserves the mounted terminal and makes no Oracle requests", async (id, features) => {
  await mount([...features], id);
  const terminal = screen.getByLabelText("Terminal input");
  fireEvent.change(terminal, { target: { value: "keep this draft" } });
  await act(async () => { fireEvent.click(tab("Overview")!); });
  expect(screen.getByRole("heading", { name: /Control tower/ })).toBeVisible();
  expect(screen.queryByLabelText("Message Oracle")).toBeNull();
  expect(screen.getByLabelText("Terminal input")).toBe(terminal);
  await act(async () => { fireEvent.click(screen.getByRole("button", { name: "← Sessions" })); });
  expect(screen.getByLabelText("Terminal input")).toBe(terminal);
  expect(terminal).toHaveValue("keep this draft");
  expect(probe.requests.filter(path => /^\/relay|^\/oracle|^\/fleet\/ask/.test(path))).toEqual([]);
});

it("returns to the mounted terminal when a selected folder loses its overview capability", async () => {
  await mount(LITE, "lite");
  const terminal = screen.getByLabelText("Terminal input");
  await act(async () => { (probe.tree.onOpenFolder as (folder: string) => void)("Team"); });
  expect(screen.getByRole("region", { name: "Folder Team" })).toBeVisible();
  probe.edition = null;
  await act(async () => { window.dispatchEvent(new Event("focus")); });
  expect(screen.queryByRole("region", { name: "Folder Team" })).toBeNull();
  expect(screen.getByLabelText("Terminal input")).toBe(terminal);
  expect(terminal).toBeVisible();
});
