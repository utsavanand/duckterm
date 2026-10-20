import { act, cleanup, renderHook } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { CONTRAST_KEY, loadContrast, resolveContrast, useTerminalContrast } from "./terminalContrast";
afterEach(() => { cleanup(); localStorage.clear(); vi.restoreAllMocks(); });
it("uses session override then nearest folder, with a readable default", () => {
  const preferences = { sessions: { special: "preserve" as const }, folders: { Work: "preserve" as const, "Work/Child": "readable" as const } };
  expect(resolveContrast(preferences, "special", "Work/Child")).toBe("preserve");
  expect(resolveContrast(preferences, "regular", "Work/Child/Nested")).toBe("readable");
  expect(resolveContrast(preferences, "regular", "Work/Other")).toBe("preserve");
  expect(resolveContrast(preferences, "regular", "WorkElsewhere")).toBe("readable");
  expect(resolveContrast(preferences, "constructor", "toString")).toBe("readable");
});
it("synchronizes mounts and restores inheritance without overwriting other preferences", () => {
  const a = renderHook(useTerminalContrast), b = renderHook(useTerminalContrast);
  act(() => a.result.current.update("folders", "Work", "preserve"));
  act(() => b.result.current.update("sessions", "local::a", "readable"));
  expect(resolveContrast(a.result.current.preferences, "local::a", "Work")).toBe("readable");
  act(() => a.result.current.update("sessions", "local::a", null));
  expect(resolveContrast(b.result.current.preferences, "local::a", "Work")).toBe("preserve");
  expect(loadContrast().folders.Work).toBe("preserve");
  localStorage.clear(); act(() => window.dispatchEvent(new StorageEvent("storage", { key: null })));
  expect(b.result.current.preferences).toEqual({ sessions: {}, folders: {} });
});
it("rejects malformed settings and does not claim success after storage failure", () => {
  localStorage.setItem(CONTRAST_KEY, '{"sessions":{"bad":3,"good":"preserve"},"folders":[]}');
  expect(loadContrast()).toEqual({ sessions: { good: "preserve" }, folders: {} });
  localStorage.setItem(CONTRAST_KEY, "invalid");
  const a = renderHook(useTerminalContrast);
  vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new Error("full"); });
  expect(() => act(() => a.result.current.update("folders", "Work", "preserve"))).toThrow("full");
  expect(a.result.current.preferences).toEqual({ sessions: {}, folders: {} });
});
