import { act, cleanup, render } from "@testing-library/react";
import { createRef } from "react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { RuntimeSetupTerminal, type RuntimeSetupTerminalHandle } from "./RuntimeSetupTerminal";

const mock = vi.hoisted(() => ({ options: {} as { linkHandler?: { activate: () => void } }, writes: [] as { data: Uint8Array | string; callback?: () => void }[], input: (_text: string) => { void _text; }, disposed: vi.fn() }));
vi.mock("@xterm/xterm", () => ({ Terminal: class {
  options = {}; cols = 80; rows = 24;
  constructor(options: typeof mock.options) { mock.options = options; }
  loadAddon() {} open() {} reset() {} dispose() { mock.disposed(); }
  write(data: Uint8Array | string, callback?: () => void) { mock.writes.push({ data, callback }); }
  onData(fn: typeof mock.input) { mock.input = fn; return { dispose() {} }; }
  onResize() { return { dispose() {} }; }
} }));
vi.mock("@xterm/addon-fit", () => ({ FitAddon: class { fit() {} } }));
beforeEach(() => {
  mock.writes = []; mock.disposed.mockClear();
  vi.stubGlobal("ResizeObserver", class { observe() {} disconnect() {} });
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

it("bounds pending output independently of scrollback and shows one fixed omission notice after draining", () => {
  const ref = createRef<RuntimeSetupTerminalHandle>();
  render(<RuntimeSetupTerminal ref={ref} active enabled onInput={vi.fn()} onResize={vi.fn()} />);
  const chunk = btoa("x".repeat(32768));
  act(() => { for (let i = 0; i < 100; i++) ref.current!.write(chunk); });
  expect(mock.writes).toHaveLength(4);
  expect(mock.writes.reduce((sum, item) => sum + item.data.length, 0)).toBe(131072);
  act(() => { for (const item of [...mock.writes]) item.callback?.(); });
  expect(mock.writes).toHaveLength(5);
  expect(mock.writes[4].data).toBe("\r\n[Some setup output was omitted because it arrived too quickly.]\r\n");
  mock.writes[4].callback?.();
  act(() => { ref.current!.write(chunk); });
  expect(mock.writes).toHaveLength(6);
});

it("disables terminal links and input when inactive, and ignores malformed or oversized frames", () => {
  const ref = createRef<RuntimeSetupTerminalHandle>(), input = vi.fn();
  const view = render(<RuntimeSetupTerminal ref={ref} active enabled={false} onInput={input} onResize={vi.fn()} />);
  mock.input("secret"); expect(input).not.toHaveBeenCalled();
  expect(mock.options.linkHandler?.activate()).toBeUndefined();
  act(() => { ref.current!.write("%%%%"); ref.current!.write("a".repeat(43693)); });
  expect(mock.writes).toHaveLength(0);
  view.rerender(<RuntimeSetupTerminal ref={ref} active enabled onInput={input} onResize={vi.fn()} />);
  mock.input("hello"); expect(input).toHaveBeenCalledWith("aGVsbG8=");
  view.unmount(); expect(mock.disposed).toHaveBeenCalledOnce();
});

it("queues clear after earlier bytes and does not reset the pending-output budget", () => {
  const ref = createRef<RuntimeSetupTerminalHandle>();
  render(<RuntimeSetupTerminal ref={ref} active enabled onInput={vi.fn()} onResize={vi.fn()} />);
  const chunk = btoa("x".repeat(32768));
  act(() => { for (let i = 0; i < 4; i++) ref.current!.write(chunk); ref.current!.clear(); ref.current!.write(chunk); });
  expect(mock.writes).toHaveLength(5);
  expect(mock.writes[4].data).toBe("\x1bc");
});
