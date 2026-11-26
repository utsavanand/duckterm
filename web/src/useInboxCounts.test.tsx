import { renderHook, cleanup } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { sessionRef } from "./hostTransport";
import { useInboxCounts } from "./useInboxCounts";
const probe = vi.hoisted(() => ({ data: {} as Record<string, { counts: Record<string, number>; unanswered_counts?: Record<string, number> }> }));
vi.mock("./useHostData", () => ({ useHostData: () => ({ data: probe.data }) }));
afterEach(cleanup);
it("separates notices from requests and keeps identical local/remote keys distinct", () => {
  probe.data = { local: { counts: { same: 3 }, unanswered_counts: { same: 1 } }, remote: { counts: { same: 2 }, unanswered_counts: { same: 2 } }, legacy: { counts: { same: 4 } } };
  const { result, rerender } = renderHook(() => useInboxCounts());
  expect(result.current.pending[sessionRef("legacy", "same")]).toBe(4);
  expect(result.current.unanswered).toEqual({ [sessionRef("local", "same")]: 1, [sessionRef("remote", "same")]: 2 });
  probe.data = { local: { counts: { same: 2 }, unanswered_counts: {} } };
  rerender();
  expect(result.current.unanswered).toEqual({});
});
