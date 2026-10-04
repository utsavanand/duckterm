import { act, cleanup, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { useDesktopNotifications } from "./useDesktopNotifications";
import { sessionRef } from "./hostTransport";
let permission: NotificationPermission;
const notices = vi.fn(), request = vi.fn();
const row = (key: string, waiting: boolean) => ({ key, label: key, waiting });
beforeEach(() => {
  localStorage.clear(); permission = "granted"; notices.mockReset(); request.mockReset();
  vi.stubGlobal("Notification", class {
    static get permission() { return permission; }
    static requestPermission = request;
    constructor(...args: unknown[]) { notices(...args); }
  });
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });
it("persists off even when browser permission remains granted", async () => {
  const first = renderHook(() => useDesktopNotifications([], ["local"]));
  expect(first.result.current.on).toBe(true);
  await act(async () => { await first.result.current.toggle(); });
  expect(localStorage.getItem("rd.notifyOn")).toBe("false"); first.unmount();
  expect(renderHook(() => useDesktopNotifications([], ["local"])).result.current.on).toBe(false);
});
it("primes loading and enabling, then notifies a witnessed transition once", async () => {
  localStorage.setItem("rd.notifyOn", "false");
  const view = renderHook(({ rows, hosts }) => useDesktopNotifications(rows, hosts), { initialProps: { rows: [] as ReturnType<typeof row>[], hosts: [] as string[] } });
  view.rerender({ rows: [row("old", true), row("new", false)], hosts: ["local"] });
  await act(async () => { await view.result.current.toggle(); });
  expect(notices).not.toHaveBeenCalled();
  view.rerender({ rows: [row("old", true), row("new", true)], hosts: ["local"] });
  expect(notices).toHaveBeenCalledTimes(1); expect(notices.mock.calls[0][0]).toBe("new needs you");
  view.rerender({ rows: [row("old", true), row("new", true)], hosts: ["local"] });
  expect(notices).toHaveBeenCalledTimes(1);
});
it("does not replay waiting sessions when a second host loads", () => {
  const view = renderHook(({ rows, hosts }) => useDesktopNotifications(rows, hosts), { initialProps: { rows: [row("x", true)], hosts: ["local"] } });
  view.rerender({ rows: [row("x", true), row(sessionRef("remote", "x"), true)], hosts: ["local", "remote"] });
  expect(notices).not.toHaveBeenCalled();
});
it("explains blocked permission and respects revocation over stored on", async () => {
  localStorage.setItem("rd.notifyOn", "true"); permission = "denied";
  const view = renderHook(() => useDesktopNotifications([], ["local"]));
  expect(view.result.current.on).toBe(false); expect(view.result.current.help).toContain("blocked in your browser");
  await act(async () => { await view.result.current.toggle(); });
  expect(request).not.toHaveBeenCalled(); expect(view.result.current.on).toBe(false);
});
it("requests and persists granted permission, then responds to revocation", async () => {
  permission = "default"; request.mockImplementation(async () => { permission = "granted"; return permission; });
  const view = renderHook(() => useDesktopNotifications([], ["local"]));
  await act(async () => { await view.result.current.toggle(); });
  expect(request).toHaveBeenCalledTimes(1); expect(view.result.current.on).toBe(true);
  expect(localStorage.getItem("rd.notifyOn")).toBe("true");
  permission = "denied"; act(() => window.dispatchEvent(new Event("focus")));
  expect(view.result.current.on).toBe(false);
});
it("reports constructor failure and missing notification support", () => {
  const view = renderHook(({ waiting }) => useDesktopNotifications([row("x", waiting)], ["local"]), { initialProps: { waiting: false } });
  notices.mockImplementation(() => { throw new Error("unavailable"); });
  view.rerender({ waiting: true });
  expect(view.result.current.help).toContain("Could not show a notification");
  view.unmount(); Reflect.deleteProperty(window, "Notification");
  const absent = renderHook(() => useDesktopNotifications([], ["local"]));
  expect(absent.result.current.on).toBe(false); expect(absent.result.current.help).toContain("unavailable");
});
