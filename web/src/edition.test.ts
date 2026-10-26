import { afterEach, expect, it, vi } from "vitest";

afterEach(() => {
  vi.unstubAllGlobals();
  vi.resetModules();
  document.querySelector('meta[name="duckterm-token"]')?.remove();
});

it("reads the server's edition with owner authentication, without choosing one locally", async () => {
  const meta = document.createElement("meta");
  meta.name = "duckterm-token"; meta.content = "fixture-owner";
  document.head.append(meta);
  vi.resetModules();
  const description = { id: "full", features: ["terminal", "oracle"] };
  const request = vi.fn().mockResolvedValue(new Response(JSON.stringify(description)));
  vi.stubGlobal("fetch", request);
  const { api } = await import("./api");
  expect(await api.edition()).toEqual(description);
  expect(request).toHaveBeenCalledWith("/edition", expect.objectContaining({
    headers: { "X-Duckterm-Token": "fixture-owner" }, cache: "no-store",
  }));
  expect(request).toHaveBeenCalledTimes(1);
});

it("reports an unavailable edition description instead of inventing a Full fallback", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response('{"error":"Unavailable"}', { status: 503 })));
  const { api } = await import("./api");
  await expect(api.edition()).rejects.toThrow("Unavailable");
});
