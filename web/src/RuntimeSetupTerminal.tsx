import { forwardRef, useEffect, useImperativeHandle, useRef } from "react";
import { Terminal } from "@xterm/xterm";
import { FitAddon } from "@xterm/addon-fit";
import { bindSetupClipboard } from "./clipboardBridge";
import "@xterm/xterm/css/xterm.css";

export type RuntimeSetupTerminalHandle = { write: (base64: string) => void; clear: () => void };

// This terminal has no session ID, WebSocket, recorder, image paste or reconnect.
// Its only transport is the initiating wizard's native-owned setup job.
export const RuntimeSetupTerminal = forwardRef<RuntimeSetupTerminalHandle, {
  active: boolean; enabled: boolean; onInput: (data: string) => void; onResize: (cols: number, rows: number) => void;
}>(function RuntimeSetupTerminal({ active, enabled, onInput, onResize }, ref) {
  const host = useRef<HTMLDivElement>(null), terminal = useRef<Terminal | null>(null), fit = useRef<FitAddon | null>(null);
  const queue = useRef({ bytes: 0, omitted: false });
  const handlers = useRef({ enabled, onInput, onResize });
  handlers.current = { enabled, onInput, onResize };
  useImperativeHandle(ref, () => ({
    write: base64 => {
      if (base64.length > 43_692) return;
      try {
        const bytes = Uint8Array.from(atob(base64), c => c.charCodeAt(0)), term = terminal.current;
        const current = queue.current;
        if (!term) return;
        if (current.bytes + bytes.length > 131_072) { current.omitted = true; return; }
        current.bytes += bytes.length;
        term.write(bytes, () => {
          if (queue.current !== current) return;
          current.bytes -= bytes.length;
          if (current.bytes === 0 && current.omitted) {
            current.omitted = false;
            const notice = "\r\n[Some setup output was omitted because it arrived too quickly.]\r\n";
            current.bytes += notice.length;
            term.write(notice, () => { if (queue.current === current) current.bytes -= notice.length; });
          }
        });
      } catch { /* Invalid data is not rendered. */ }
    },
    clear: () => {
      queue.current.omitted = false;
      // Queue the reset after earlier writes; reset() itself is synchronous
      // and would let old buffered output reappear in the next job.
      terminal.current?.write("\x1bc");
    },
  }), []);
  useEffect(() => {
    if (!host.current) return;
    const term = new Terminal({ fontSize: 12, fontFamily: 'ui-monospace, "SFMono-Regular", monospace', scrollback: 1000,
      convertEol: false, cursorBlink: true, disableStdin: !handlers.current.enabled,
      theme: { background: "#0b0f13", foreground: "#e1e5e9", cursor: "#8bcbb0" },
      // Installer text never launches a URL or invokes a session clipboard action.
      linkHandler: { activate: () => undefined },
    });
    const addon = new FitAddon(); term.loadAddon(addon); term.open(host.current);
    terminal.current = term; fit.current = addon;
    const releaseClipboard = bindSetupClipboard(term, () => handlers.current.enabled);
    const measure = () => { if (host.current?.clientWidth && host.current.clientHeight) addon.fit(); };
    const observer = new ResizeObserver(measure); observer.observe(host.current); measure();
    const input = term.onData(text => {
      if (!handlers.current.enabled) return;
      const bytes = new TextEncoder().encode(text);
      for (let offset = 0; offset < bytes.length; offset += 32_768) {
        handlers.current.onInput(btoa(String.fromCharCode(...bytes.subarray(offset, offset + 32_768))));
      }
    });
    const resize = term.onResize(({ cols, rows }) => handlers.current.onResize(Math.min(500, cols), Math.min(300, rows)));
    handlers.current.onResize(Math.min(500, term.cols), Math.min(300, term.rows));
    return () => { releaseClipboard(); observer.disconnect(); input.dispose(); resize.dispose(); term.dispose(); queue.current = { bytes: 0, omitted: false }; terminal.current = null; fit.current = null; };
  }, []);
  useEffect(() => {
    if (terminal.current) terminal.current.options.disableStdin = !enabled;
    if (active) fit.current?.fit();
  }, [active, enabled]);
  return <div className="rd-setup-terminal" aria-label="Agent setup terminal" hidden={!active}><div className="rd-setup-terminal-host" ref={host} /></div>;
});
