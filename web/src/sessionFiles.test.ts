import { afterEach, expect, it, vi } from "vitest";
import { localFileSession, sessionFiles, FileRequestError } from "./sessionFiles";
vi.mock("./api", () => ({ authHeaders: () => ({ Authorization: "Bearer test-only" }) }));
afterEach(() => vi.unstubAllGlobals());
it.each([{ key: "same-local-key", host: "server" }, { key: "~remote~736572766572~6167656e74" }, { key: "~remote~736572766572~6167656e74", host: "local" }])("rejects remote identity before a request: %j", async session => {
  const fetch = vi.fn(); vi.stubGlobal("fetch", fetch);
  expect(localFileSession(session)).toBe(false);
  await expect(sessionFiles.list(session)).rejects.toThrow("Remote");
  await expect(sessionFiles.content(session, "report.md")).rejects.toThrow("Remote");
  expect(fetch).not.toHaveBeenCalled();
});
it("encodes relative paths and opaque cursors, preserving cancellation and auth", async () => {
  const fetch = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ available: true }) }); vi.stubGlobal("fetch", fetch);
  const controller = new AbortController();
  await sessionFiles.list({ key: "local key", host: "local" }, "a/b & c", "opaque+/=", controller.signal);
  const [url, options] = fetch.mock.calls[0];
  const parsed = new URL(url, "http://localhost");
  expect(parsed.pathname).toBe("/sessions/local%20key/files");
  expect(parsed.searchParams.get("path")).toBe("a/b & c");
  expect(parsed.searchParams.get("cursor")).toBe("opaque+/=");
  expect(options).toMatchObject({ headers: { Authorization: "Bearer test-only" }, cache: "no-store", signal: controller.signal });
});
it("preserves cursor conflict status so the UI discards old pages", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: false, status: 409, json: async () => ({ error: "Directory changed" }) }));
  await expect(sessionFiles.list({ key: "local" })).rejects.toEqual(new FileRequestError("Directory changed", 409));
});
