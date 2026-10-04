import { act, cleanup, renderHook } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { useNow } from "./useNow";
afterEach(() => { cleanup(); vi.useRealTimers(); Reflect.deleteProperty(document, "visibilityState"); });
function visibility(value: string) { Object.defineProperty(document, "visibilityState", { value, configurable: true }); document.dispatchEvent(new Event("visibilitychange")); }
it("stops display clocks while inactive or hidden and catches up immediately on return", () => {
  vi.useFakeTimers(); vi.setSystemTime(1000); visibility("visible");
  const view = renderHook(({ active }) => useNow(1000, active), { initialProps: { active: true } });
  act(() => vi.advanceTimersByTime(1000)); expect(view.result.current).toBe(2000);
  act(() => visibility("hidden")); expect(vi.getTimerCount()).toBe(0);
  act(() => vi.advanceTimersByTime(10_000)); expect(view.result.current).toBe(2000);
  act(() => visibility("visible")); expect(view.result.current).toBe(12_000);
  view.rerender({ active: false }); expect(vi.getTimerCount()).toBe(0);
  act(() => vi.advanceTimersByTime(10_000)); view.rerender({ active: true }); expect(view.result.current).toBe(22_000);
  view.unmount(); expect(vi.getTimerCount()).toBe(0);
});
it("keeps background voice time running", () => {
  vi.useFakeTimers(); vi.setSystemTime(1000); visibility("hidden");
  const view = renderHook(() => useNow(1000, true, false));
  act(() => vi.advanceTimersByTime(90_000)); expect(view.result.current).toBe(91_000);
});
