import { act, cleanup, renderHook } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { STYLING_KEY, loadStyling, resolveStyling, useTerminalStyling } from "./terminalStyling";
afterEach(() => { cleanup(); localStorage.clear(); vi.restoreAllMocks(); });
it("uses session override then nearest folder, with a enhanced default", () => {
  const preferences = { sessions: { special: "program" as const }, folders: { Work: "program" as const, "Work/Child": "enhanced" as const } };
  expect(resolveStyling(preferences, "special", "Work/Child")).toBe("program");
  expect(resolveStyling(preferences, "regular", "Work/Child/Nested")).toBe("enhanced");
  expect(resolveStyling(preferences, "regular", "Work/Other")).toBe("program");
  expect(resolveStyling(preferences, "regular", "WorkElsewhere")).toBe("enhanced");
  expect(resolveStyling(preferences, "constructor", "toString")).toBe("enhanced");
});
it("synchronizes mounts and restores inheritance without overwriting other preferences", () => {
  const a = renderHook(useTerminalStyling), b = renderHook(useTerminalStyling);
  act(() => a.result.current.update("folders", "Work", "program"));
  act(() => b.result.current.update("sessions", "local::a", "enhanced"));
  expect(resolveStyling(a.result.current.preferences, "local::a", "Work")).toBe("enhanced");
  act(() => a.result.current.update("sessions", "local::a", null));
  expect(resolveStyling(b.result.current.preferences, "local::a", "Work")).toBe("program");
  expect(loadStyling().folders.Work).toBe("program");
  localStorage.clear(); act(() => window.dispatchEvent(new StorageEvent("storage", { key: null })));
  expect(b.result.current.preferences).toEqual({ sessions: {}, folders: {} });
});
it("rejects malformed settings and does not claim success after storage failure", () => {
  localStorage.setItem(STYLING_KEY, '{"sessions":{"bad":3,"good":"program"},"folders":[]}');
  expect(loadStyling()).toEqual({ sessions: { good: "program" }, folders: {} });
  localStorage.setItem(STYLING_KEY, "invalid");
  const a = renderHook(useTerminalStyling);
  vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new Error("full"); });
  expect(() => act(() => a.result.current.update("folders", "Work", "program"))).toThrow("full");
  expect(a.result.current.preferences).toEqual({ sessions: {}, folders: {} });
});
