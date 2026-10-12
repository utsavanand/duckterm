import { afterEach, expect, it, vi } from "vitest";
import { api } from "./api";
import { sessionRef } from "./hostTransport";
afterEach(() => { delete window.webkit; vi.unstubAllGlobals(); });
it("sends checkpoint identity and reads its receipt on the selected remote without local fallback", async () => {
  const local = vi.fn(); vi.stubGlobal("fetch", local);
  const remote = vi.fn().mockResolvedValue({ status: 200, body: '{"request_id":"attempt-1","state":"running"}' });
  window.webkit = { messageHandlers: { launchRequest: { postMessage: remote } } };
  const key = sessionRef("remote-machine", "same.id");
  await api.checkpoint(key, "manual", "attempt-1");
  expect(remote).toHaveBeenLastCalledWith({ target: "remote-machine", operation: "session-request", params: { path: "/sessions/same.id/checkpoint", method: "POST", body: '{"label":"manual","request_id":"attempt-1"}' } });
  expect(await api.checkpointAttempt(key, "attempt-1")).toEqual({ request_id: "attempt-1", state: "running" });
  expect(remote).toHaveBeenLastCalledWith({ target: "remote-machine", operation: "session-request", params: { path: "/sessions/same.id/checkpoint-attempts/attempt-1", method: "GET" } });
  remote.mockRejectedValue(new Error("Unsupported session operation"));
  await expect(api.checkpointAttempt(key, "attempt-1")).rejects.toThrow("Unsupported session operation");
  expect(local).not.toHaveBeenCalled();
});
it.each([401, 403, 404, 405, 410, 501])("does not infer a checkpoint outcome from receipt HTTP%s", async status => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response('{}', { status })));
  expect(await api.checkpointAttempt("local", "attempt-1")).toBeNull();
});
