import { afterEach, expect, it, vi } from "vitest";
import { hostFetch, routedFetch, sessionRef, splitSessionRef, setRemoteGroup, remoteGroups } from "./hostTransport";
import { reduce } from "./useEventStream";
import { PersistedSession } from "./types";

afterEach(() => { delete window.webkit; vi.unstubAllGlobals(); localStorage.clear(); });

it("routes equal session keys to different machines without changing local requests", async () => {
  const local = vi.fn().mockResolvedValue(new Response("{}"));
  vi.stubGlobal("fetch", local);
  const remote = vi.fn().mockResolvedValue({ status: 200, body: JSON.stringify({ session_key: "fork-child" }) });
  window.webkit = { messageHandlers: { launchRequest: { postMessage: remote } } };
  const key = sessionRef("user@remote-host", "same.id");
  expect(splitSessionRef(key)).toEqual({ host: "user@remote-host", key: "same.id" });
  await routedFetch("/sessions/same.id/stop", { method: "POST", body: "{}" });
  const result = await routedFetch(`/sessions/${encodeURIComponent(key)}/fork`, { method: "POST", body: "{}" });
  expect(local).toHaveBeenCalledWith("/sessions/same.id/stop", { method: "POST", body: "{}" });
  expect(remote).toHaveBeenCalledWith({ target: "user@remote-host", operation: "session-request", params: { path: "/sessions/same.id/fork", method: "POST", body: "{}" } });
  expect(await result.json()).toEqual({ session_key: sessionRef("user@remote-host", "fork-child") });
});

it("preserves local rows through remote snapshots, disconnection, deletion and reconnect", () => {
  const row: PersistedSession = { session_key: "same", state: "busy", event_count: 1, started_at: 1, updated_at: 1 };
  let state = reduce({ sessions: new Map(), tombstoned: new Set() }, { kind: "seed", sessions: [row] });
  const local = state.sessions.get("same");
  const key = sessionRef("remote", "same");
  setRemoteGroup(key, "Sotto");
  const snapshot = { kind: "remote-snapshot" as const, host: "remote", label: "Remote", sessions: [{ ...row, session_key: key }], groups: remoteGroups() };
  state = reduce(state, snapshot);
  expect(state.sessions.size).toBe(2);
  expect(state.sessions.get(key)?.group).toBe("Sotto");
  state = reduce(state, { kind: "remote-offline", host: "remote" });
  expect(state.sessions.get(key)?.hostOffline).toBe(true);
  expect(state.sessions.get("same")).toBe(local);
  state = reduce(state, snapshot);
  expect(state.sessions.get(key)?.hostOffline).toBe(false);
  state = reduce(state, { kind: "remove", keys: [key] });
  state = reduce(state, snapshot);
  expect([...state.sessions.keys()]).toEqual(["same"]);
  expect(state.sessions.get("same")).toBe(local);
});

it("does not forward local credentials to the native bridge", async () => {
  const remote = vi.fn().mockResolvedValue({ status: 403, body: '{"error":"denied"}' });
  window.webkit = { messageHandlers: { launchRequest: { postMessage: remote } } };
  const result = await hostFetch("remote", "/sessions", { headers: { "X-Duckterm-Token": "local-secret" } });
  expect(result.status).toBe(403);
  expect(JSON.stringify(remote.mock.calls)).not.toContain("local-secret");
});

it("allows typing into a quiet native terminal as soon as its handshake completes", async () => {
  const { terminalSocket } = await import("./hostTransport");
  const request = vi.fn().mockResolvedValue({ opened: true });
  window.webkit = { messageHandlers: { launchRequest: { postMessage: request } } };
  const socket = terminalSocket(sessionRef("remote", "quiet"));
  const opened = vi.fn(); socket.onopen = opened;
  const id = request.mock.calls[0][0].params.id;
  window.dispatchEvent(new CustomEvent("remote-terminal", { detail: { id, opened: true } }));
  expect(opened).toHaveBeenCalledOnce();
  expect(socket.readyState).toBe(1);
  socket.send(new TextEncoder().encode("hello\n"));
  await vi.waitFor(() => expect(request).toHaveBeenCalledWith({ target: "remote", operation: "terminal-send", params: { id, data: btoa("hello\n") } }));
  socket.close();
  await vi.waitFor(() => expect(request).toHaveBeenCalledWith({ target: "remote", operation: "terminal-close", params: { id } }));
});

it("preserves remote ZIP bytes instead of decoding binary as text", async () => {
  const bytes = new Uint8Array([80, 75, 0, 255, 128, 192, 254, 1]);
  const remote = vi.fn().mockResolvedValue({ status: 200, base64: btoa(String.fromCharCode(...bytes)), contentType: "application/zip" });
  window.webkit = { messageHandlers: { launchRequest: { postMessage: remote } } };
  const response = await hostFetch("remote", "/bugreport/bundles/" + "a".repeat(32));
  expect(response.headers.get("Content-Type")).toBe("application/zip");
  expect(new Uint8Array(await response.arrayBuffer())).toEqual(bytes);
});
