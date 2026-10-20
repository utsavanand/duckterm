import { afterEach, expect, it, vi } from "vitest";
import { memoryGet, MemoryReadError } from "./memoryViewApi";
import { sessionFetch, sessionRef } from "./hostTransport";

vi.mock("./api", () => ({ authHeaders: () => ({ "X-Duckterm-Token": "test" }) }));
vi.mock("./hostTransport", async original => ({
  ...await original<typeof import("./hostTransport")>(), sessionFetch: vi.fn(),
}));
const fetchMemory = vi.mocked(sessionFetch);
afterEach(() => vi.resetAllMocks());

it.each<Record<string, string>>([
  { checkpoint: "cp/one", revisions_cursor: "opaque:20", revisions_limit: "20" },
  { claims_cursor: "claims:25", claims_limit: "25", revisions_cursor: "history:20" },
  { revision: "exact/old", revisions_limit: "100" },
])("preserves the selected host and exact read query %j", async query => {
  const key = sessionRef("other-host", "same/id");
  fetchMemory.mockResolvedValue(new Response(JSON.stringify({ scope: "current" })));
  await memoryGet(key, "", query);
  const [ref, path, options] = fetchMemory.mock.calls[0];
  expect(ref).toBe(key);
  const url = new URL(path, "http://localhost");
  expect(url.pathname).toBe("/sessions/same%2Fid/memory");
  expect(Object.fromEntries(url.searchParams)).toEqual(query);
  expect(options).toMatchObject({ cache: "no-store" });
  expect(options?.body).toBeUndefined();
  expect(fetchMemory).toHaveBeenCalledOnce();
});

it.each<Record<string, string>>([
  { checkpoint: "cp", revision: "r" },
  { checkpoint: "cp", claims_cursor: "cursor" },
  { checkpoint: "cp", claims_limit: "25" },
  { revision: "r", claims_cursor: "cursor" },
  { claims_limit: "0" }, { claims_limit: "101" }, { revisions_limit: "1.5" },
  { claims_cursor: "" }, { unknown: "value" },
])("rejects an invalid scope or page request without fetching %j", async query => {
  await expect(memoryGet("session", "", query)).rejects.toMatchObject({ status: 400 });
  expect(fetchMemory).not.toHaveBeenCalled();
});

it("preserves changed-snapshot 409 for explicit reload without automatically fetching a new page", async () => {
  fetchMemory.mockResolvedValue(new Response(JSON.stringify({ error: "Saved memory changed; reload its first page" }), { status: 409 }));
  await expect(memoryGet("session", "", { claims_cursor: "old:25" })).rejects.toMatchObject({
    status: 409, message: "Saved memory changed; reload its first page",
  });
  expect(fetchMemory).toHaveBeenCalledOnce();
});

it("retains evidence-read pagination separately from memory-view query validation", async () => {
  fetchMemory.mockResolvedValue(new Response(JSON.stringify({ text: "evidence", next_offset: null })));
  await expect(memoryGet("session", "read", { source: "source:v1", record: "r1", offset: "25" })).resolves.toMatchObject({ text: "evidence" });
});

it("keeps HTTP status on an unstructured error and rejects malformed success", async () => {
  fetchMemory.mockResolvedValueOnce(new Response("Unavailable", { status: 503 }));
  await expect(memoryGet("session")).rejects.toMatchObject({ status: 503 });
  fetchMemory.mockResolvedValueOnce(new Response("broken"));
  await expect(memoryGet("session")).rejects.toBeInstanceOf(MemoryReadError);
});
