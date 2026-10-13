import { afterEach, expect, it, vi } from "vitest";
import { api } from "./api";
import { sessionRef } from "./hostTransport";

afterEach(() => { delete window.webkit; vi.unstubAllGlobals(); });

it("keeps checkpoint preview, confirmed selection and receipt on the chosen remote", async () => {
  const local = vi.fn(); vi.stubGlobal("fetch", local);
  const remote = vi.fn().mockResolvedValue({ status: 200, body: '{"pending_records":47,"selection_plan":{"version":1}}' });
  window.webkit = { messageHandlers: { launchRequest: { postMessage: remote } } };
  const key = sessionRef("remote-machine", "same.id");
  const preview = await api.checkpointPlan(key);
  expect(remote).toHaveBeenLastCalledWith({ target: "remote-machine", operation: "session-request", params: { path: "/sessions/same.id/checkpoint-plan", method: "GET" } });
  const selection = { plan: preview.selection_plan, records: "all" as const };
  await api.checkpoint(key, "manual", "chosen-1", selection);
  expect(remote).toHaveBeenLastCalledWith({ target: "remote-machine", operation: "session-request", params: { path: "/sessions/same.id/checkpoint", method: "POST", body: JSON.stringify({ label: "manual", request_id: "chosen-1", selection }) } });
  await api.checkpointAttempt(key, "chosen-1");
  expect(remote).toHaveBeenLastCalledWith({ target: "remote-machine", operation: "session-request", params: { path: "/sessions/same.id/checkpoint-attempts/chosen-1", method: "GET" } });
  remote.mockRejectedValue(new Error("Unsupported session operation"));
  await expect(api.checkpointPlan(key)).rejects.toThrow("Unsupported session operation");
  expect(local).not.toHaveBeenCalled();
});
