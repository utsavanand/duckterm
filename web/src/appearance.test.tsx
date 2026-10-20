import { act, cleanup, renderHook } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { APPEARANCE_KEY, DEFAULT_APPEARANCE, loadAppearance, normalizeAppearance, useAppearance } from "./appearance";

afterEach(() => { cleanup(); localStorage.clear(); vi.restoreAllMocks(); });
it("recovers corrupt preferences and rejects unknown fonts and invalid sizes", () => {
  localStorage.setItem(APPEARANCE_KEY, "{broken");
  expect(loadAppearance()).toEqual(DEFAULT_APPEARANCE);
  expect(normalizeAppearance({ uiFont: "__proto__", terminalFont: "constructor", uiSize: "18", terminalSize: null })).toEqual(DEFAULT_APPEARANCE);
  expect(normalizeAppearance({ uiFont: "arial", terminalFont: "monaco", uiSize: 200, terminalSize: -4 })).toEqual({ uiFont: "arial", terminalFont: "monaco", uiSize: 20, terminalSize: 10 });
});
it("persists and synchronizes independent consumers without losing other settings", () => {
  const one = renderHook(useAppearance), two = renderHook(useAppearance);
  act(() => one.result.current.update({ uiSize: 18 }));
  act(() => two.result.current.update({ terminalFont: "monaco" }));
  expect(one.result.current.appearance).toEqual(two.result.current.appearance);
  expect(loadAppearance()).toMatchObject({ uiSize: 18, terminalFont: "monaco" });
  localStorage.setItem(APPEARANCE_KEY, JSON.stringify({ terminalSize: 22 }));
  act(() => window.dispatchEvent(new StorageEvent("storage", { key: APPEARANCE_KEY })));
  expect(one.result.current.appearance.terminalSize).toBe(22);
  localStorage.clear(); act(() => window.dispatchEvent(new StorageEvent("storage", { key: null })));
  expect(two.result.current.appearance).toEqual(DEFAULT_APPEARANCE);
});
it("does not report or apply a change when saving fails", () => {
  const hook = renderHook(useAppearance);
  vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new Error("unavailable"); });
  expect(() => act(() => hook.result.current.update({ uiSize: 20 }))).toThrow("unavailable");
  expect(hook.result.current.appearance).toEqual(DEFAULT_APPEARANCE);
});
