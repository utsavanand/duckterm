import { act, cleanup, render } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { Terminal } from "./Terminal";
const mocks = vi.hoisted(() => ({ fit: vi.fn(), send: vi.fn(), socket: { readyState: 1, send: vi.fn(), close: vi.fn() } }));
vi.mock("./hostTransport", () => ({ terminalSocket: () => mocks.socket, sessionFetch: vi.fn() }));
vi.mock("./clipboardBridge", () => ({ bindClipboardBridge: vi.fn(), releaseClipboardBridge: vi.fn(), imagePathText: vi.fn() }));
vi.mock("@xterm/addon-fit", () => ({ FitAddon: class { fit = mocks.fit; } }));
vi.mock("@xterm/xterm", () => ({ Terminal: class {
  cols = 80; rows = 24; options = {}; loadAddon() {} open() {} refresh() {} dispose() {}
  onData() { return { dispose() {} }; } attachCustomKeyEventHandler() {}
} }));
let resize: () => void;
let width = 0;
let frames: Map<number, FrameRequestCallback>;
let seq = 0;
beforeEach(() => {
  frames = new Map(); width = 0; vi.clearAllMocks();
  vi.spyOn(HTMLElement.prototype, "clientWidth", "get").mockImplementation(() => width);
  vi.spyOn(HTMLElement.prototype, "clientHeight", "get").mockReturnValue(500);
  vi.stubGlobal("ResizeObserver", class { constructor(callback: () => void) { resize = callback; } observe() {} disconnect() {} });
  vi.spyOn(window, "requestAnimationFrame").mockImplementation(callback => { frames.set(++seq, callback); return seq; });
  vi.spyOn(window, "cancelAnimationFrame").mockImplementation(id => { frames.delete(id); });
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });
const frame = () => act(() => { const work = [...frames.values()]; frames.clear(); work.forEach(callback => callback(0)); });
it("retries an unmeasurable observer callback without needing a second resize event", () => {
  render(<Terminal sessionKey="resize-fixture" />);
  mocks.fit.mockClear(); mocks.socket.send.mockClear();
  act(() => resize());
  expect(mocks.fit).not.toHaveBeenCalled(); expect(frames.size).toBe(1);
  frame(); expect(mocks.fit).not.toHaveBeenCalled(); expect(frames.size).toBe(1);
  width = 800; frame();
  expect(mocks.fit).toHaveBeenCalledTimes(1);
  expect(mocks.socket.send).toHaveBeenCalledWith(JSON.stringify({ resize: { cols: 80, rows: 24 } }));
  expect(frames.size).toBe(0);
});
it("cancels a deferred fit while hidden and on unmount", () => {
  const view = render(<Terminal sessionKey="hidden-fixture" />);
  expect(frames.size).toBe(1);
  view.rerender(<Terminal sessionKey="hidden-fixture" active={false} />);
  expect(frames.size).toBe(0);
  view.rerender(<Terminal sessionKey="hidden-fixture" active />);
  expect(frames.size).toBe(1);
  view.unmount(); expect(frames.size).toBe(0);
});
