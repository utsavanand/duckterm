import { afterEach, expect, it, vi } from "vitest";
import { routedFetch } from "./hostTransport";
import { api } from "./api";

vi.mock("./hostTransport", () => ({ routedFetch: vi.fn(), sessionFetch: vi.fn(), splitSessionRef: vi.fn(), setRemoteGroup: vi.fn(), changeRemoteFolders: vi.fn() }));
afterEach(() => vi.resetAllMocks());

it("surfaces backend model discovery guidance and can retry successfully", async () => {
  vi.mocked(routedFetch).mockResolvedValueOnce(new Response(JSON.stringify({ error: "Check CLI sign-in and retry." }), { status: 503, statusText: "Service Unavailable" }));
  await expect(api.models("session-a")).rejects.toThrow("Check CLI sign-in and retry.");
  const result = { models: [{ id: "gpt-6-astra", label: "GPT-6-Astra" }] };
  vi.mocked(routedFetch).mockResolvedValueOnce(new Response(JSON.stringify(result)));
  await expect(api.models("session-a")).resolves.toEqual(result);
});

it.each(["<html>upstream unavailable</html>", "null", "{}", '{"error":12}', '{"error":"  "}'])("retains HTTP status when no usable error is returned: %s", async body => {
  vi.mocked(routedFetch).mockResolvedValue(new Response(body, { status: 503, statusText: "Service Unavailable" }));
  await expect(api.models("session-a")).rejects.toThrow("503 Service Unavailable");
});
