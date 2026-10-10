import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { App } from "./App";
import { api } from "./api";
import { effectiveState } from "./sessions";
import { SessionView, viewFromPersisted } from "./types";
import { VoiceSession, Speaker } from "./voice";

const probe = vi.hoisted(() => ({
  terminal: vi.fn(), connectors: vi.fn(), voice: [] as VoiceSession[],
  sessions: [] as SessionView[], hosts: ["local"], folders: ["Team"], counts: {},
  refresh: vi.fn(), remove: vi.fn(), patch: vi.fn(), notes: [],
  requests: [] as { id: string; session_key: string; name: string; deadline: number; status: string }[],
}));
vi.mock("./useEventStream", () => ({ useEventStream: () => ({ sessions: probe.sessions, connected: true, loadedHosts: probe.hosts, removeSessions: probe.remove, patchSession: probe.patch }) }));
vi.mock("./useInboxCounts", () => ({ useInboxCounts: () => probe.counts }));
vi.mock("./useFolders", () => ({ useFolders: () => ({ folders: probe.folders, refreshFolders: probe.refresh }) }));
vi.mock("./relay", () => ({ useRelayCount: () => 0, useRelay: () => ({ notes: probe.notes, loaded: true }) }));
vi.mock("./Terminal", () => ({ Terminal: () => { probe.terminal(); return <input aria-label="Terminal input" />; } }));
vi.mock("./Connectors", () => ({ Connectors: () => { probe.connectors(); return null; } }));
vi.mock("./ContextPanel", () => ({ ContextPanel: () => null }));
vi.mock("./SessionCard", () => ({ SessionCard: () => null }));
vi.mock("./AgentTree", () => ({ AgentTree: ({ sessions, now }: { sessions: SessionView[]; now: number }) => <div data-testid="states">{sessions.map(s => effectiveState(s, now)).join(",")}</div> }));
vi.mock("./VoiceControl", async importOriginal => {
  const actual = await importOriginal<typeof import("./VoiceControl")>();
  return { ...actual, useVoice: (sessions: VoiceSession[], speaker?: Speaker) => { probe.voice = sessions; return actual.useVoice(sessions, speaker); } };
});

beforeEach(() => {
  vi.useFakeTimers(); vi.setSystemTime(100_000); localStorage.clear(); probe.requests = [];
  Object.defineProperty(document, "visibilityState", { value: "visible", configurable: true });
  vi.stubGlobal("ResizeObserver", class { observe() {} disconnect() {} });
  vi.stubGlobal("matchMedia", () => ({ matches: false, addEventListener() {}, removeEventListener() {} }));
  vi.stubGlobal("fetch", vi.fn(async (path: string) => ({ ok: true, json: async () => String(path).includes("archive-requests") ? { requests: probe.requests } : String(path).endsWith("/shell") ? { open: false } : { pins: [] } })));
  vi.spyOn(api, "voiceStatus").mockResolvedValue({ state: "absent", size: "310 MB" });
  probe.sessions = Array.from({ length: 30 }, (_, i) => ({
    ...viewFromPersisted({ session_key: `test-${i}`, name: `test-${i}`, state: "busy", started_at: 1, updated_at: 100_000, event_count: 1 } as never),
    group: "Team", ptyOwned: true, idleSince: 100_000,
  }));
});
afterEach(() => { cleanup(); vi.useRealTimers(); vi.restoreAllMocks(); vi.unstubAllGlobals(); Reflect.deleteProperty(document, "visibilityState"); probe.terminal.mockClear(); probe.connectors.mockClear(); });
async function elapse(seconds: number) {
  // Separate commits per second: batching a whole minute in one act hides
  // the production render frequency this regression is meant to catch.
  for (let i = 0; i < seconds; i++) await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
}
it("keeps 30-session clock ticks and unchanged archive polls out of terminal/connector renders, preserving 30s/90s settling", async () => {
  render(<App />); await act(async () => {});
  const terminalRenders = probe.terminal.mock.calls.length;
  const connectorRenders = probe.connectors.mock.calls.length;
  fireEvent.change(screen.getByLabelText("Terminal input"), { target: { value: "keep my draft" } });
  await elapse(29);
  expect(screen.getByTestId("states").textContent).toContain("busy");
  await elapse(1);
  expect(screen.getByTestId("states").textContent?.split(",")).toEqual(Array(30).fill("idle"));
  expect(probe.voice.every(s => s.state === "busy")).toBe(true);
  await elapse(60);
  expect(probe.voice.every(s => s.state === "idle")).toBe(true);
  expect(probe.terminal).toHaveBeenCalledTimes(terminalRenders);
  expect(probe.connectors).toHaveBeenCalledTimes(connectorRenders);
  expect(screen.getByLabelText("Terminal input")).toHaveValue("keep my draft");
  expect(document.title).toBe("DuckTerm");
});
it("keeps archive countdown live with unchanged responses and reflects real archive and waiting changes", async () => {
  probe.requests = [{ id: "archive-1", session_key: "test-29", name: "test-29", deadline: 110_000, status: "pending" }];
  const view = render(<App />); await act(async () => {});
  expect(screen.getByText("10s")).toBeInTheDocument();
  const baseline = probe.terminal.mock.calls.length;
  await act(async () => { await vi.advanceTimersByTimeAsync(3000); });
  expect(screen.getByText("7s")).toBeInTheDocument(); expect(probe.terminal).toHaveBeenCalledTimes(baseline);
  probe.requests = [];
  await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
  expect(screen.queryByLabelText("Archive notification for test-29")).toBeNull();
  probe.sessions = probe.sessions.map((s, i) => i ? s : { ...s, state: "waiting" });
  view.rerender(<App />); await act(async () => {});
  expect(document.title).toBe("(1) DuckTerm");
});
