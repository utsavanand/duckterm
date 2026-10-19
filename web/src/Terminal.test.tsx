import { act, cleanup, render } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { Terminal } from "./Terminal";
import type { TerminalSocket } from "./hostTransport";
const mocks = vi.hoisted(() => ({
  fit: vi.fn(), resize: vi.fn(), write: vi.fn(),
  socket: { readyState: 1, binaryType: "arraybuffer", send: vi.fn(), close: vi.fn(),
    onopen: null, onclose: null, onmessage: null } as TerminalSocket,
}));
vi.mock("./hostTransport", () => ({ terminalSocket: () => mocks.socket, sessionFetch: vi.fn() }));
vi.mock("./clipboardBridge", () => ({ bindClipboardBridge: vi.fn(), releaseClipboardBridge: vi.fn(), imagePathText: vi.fn() }));
vi.mock("@xterm/addon-fit", () => ({ FitAddon: class { fit = mocks.fit; } }));
vi.mock("@xterm/xterm", () => ({ Terminal: class {
  cols = 80; rows = 24; options = {}; loadAddon() {} open() {} refresh() {} dispose() {}
  resize(cols: number, rows: number) { this.cols = cols; this.rows = rows; mocks.resize(cols, rows); }
  write = mocks.write;
  scrollToBottom() {} scrollLines() {}
  onData() { return { dispose() {} }; } attachCustomKeyEventHandler() {}
} }));
let resize: () => void;
let width = 0;
let frames: Map<number, FrameRequestCallback>;
let seq = 0;
beforeEach(() => {
  frames = new Map(); width = 0; vi.clearAllMocks(); mocks.fit.mockReset();
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
  mocks.fit.mockClear(); vi.mocked(mocks.socket.send).mockClear();
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

const message = (data: ArrayBuffer | string) => act(() => mocks.socket.onmessage?.({ data }));
const replayParsed = () => act(() => mocks.write.mock.calls.find(call => typeof call[1] === "function")?.[1]());

it("holds captured geometry through parser completion, then fits and sends the visible size", () => {
  width = 800;
  render(<Terminal sessionKey="snapshot-fixture" />);
  mocks.fit.mockImplementation(() => mocks.resize(110, 30));
  mocks.fit.mockClear(); mocks.socket.send = vi.fn();
  message('{"snapshot":{"cols":120,"rows":40}}');
  expect(mocks.resize).toHaveBeenCalledWith(120, 40);
  expect(mocks.write).not.toHaveBeenCalled();
  act(() => resize());
  expect(mocks.fit).not.toHaveBeenCalled();
  const snapshot = new Uint8Array([27, 91, 72, 65]).buffer;
  message(snapshot);
  act(() => resize());
  expect(mocks.write.mock.calls[0][0]).toEqual(new Uint8Array(snapshot));
  expect(mocks.fit).not.toHaveBeenCalled();
  expect(mocks.socket.send).not.toHaveBeenCalled();
  replayParsed();
  expect(mocks.fit).toHaveBeenCalledOnce();
  expect(mocks.socket.send).toHaveBeenCalledOnce();
  message('{"snapshot":{"cols":999,"rows":999}}');
  expect(mocks.resize).not.toHaveBeenCalledWith(999, 999);
});

it("leaves hidden snapshot geometry intact until activation", () => {
  width = 800;
  const view = render(<Terminal sessionKey="hidden-snapshot" />);
  message('{"snapshot":{"cols":120,"rows":40}}');
  view.rerender(<Terminal sessionKey="hidden-snapshot" active={false} />);
  mocks.fit.mockClear();
  message(new ArrayBuffer(1)); replayParsed();
  expect(mocks.fit).not.toHaveBeenCalled();
  view.rerender(<Terminal sessionKey="hidden-snapshot" active />);
  expect(mocks.fit).toHaveBeenCalledOnce();
});

it("ignores malformed or repeated controls without consuming the first binary snapshot", () => {
  width = 800;
  render(<Terminal sessionKey="invalid-metadata" />);
  for (const data of ['not-json', 'null', '{}', '{"snapshot":{"cols":0,"rows":40}}',
    '{"snapshot":{"cols":80.5,"rows":40}}', '{"snapshot":{"cols":80,"rows":1001}}',
    '{"snapshot":{"cols":"80","rows":40}}']) message(data);
  expect(mocks.resize).not.toHaveBeenCalled();
  expect(mocks.write).not.toHaveBeenCalled();
  message('{"snapshot":{"cols":120,"rows":40}}');
  message('{"snapshot":{"cols":90,"rows":30}}');
  expect(mocks.resize).toHaveBeenCalledTimes(1);
  message(new ArrayBuffer(1));
  expect(mocks.write.mock.calls[0][1]).toBeTypeOf("function");
});

it("still parses binary-only streams and lets live writes follow in order", () => {
  width = 800;
  render(<Terminal sessionKey="legacy-stream" />);
  mocks.fit.mockClear();
  message(new Uint8Array([65]).buffer);
  message(new Uint8Array([66]).buffer);
  expect(mocks.write.mock.calls[0]).toEqual([new Uint8Array([65]), expect.any(Function)]);
  expect(mocks.write.mock.calls[1]).toEqual([new Uint8Array([66])]);
  replayParsed();
  expect(mocks.fit).toHaveBeenCalledOnce();
});
