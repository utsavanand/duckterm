import { useEffect, useRef } from "react";
import { Terminal as Xterm } from "@xterm/xterm";
import { FitAddon } from "@xterm/addon-fit";
import "@xterm/xterm/css/xterm.css";
import { DEFAULT_TERM_THEME, TERM_THEMES } from "./termThemes";

// A real terminal for a launched session: xterm.js over the
// /sessions/:key/terminal WebSocket. Raw PTY bytes stream in as binary frames
// and render with full ANSI/cursor support; keystrokes go back as binary
// frames; resize goes back as a text JSON control message. This is the
// terminal-forward surface — the agent's TUI (claude, codex) renders here as
// it would in iTerm.
//
// The WS is a GET, so it rides the same 127.0.0.1 loopback gate as the rest of
// the GET API — no token needed (only state-changing POSTs are token-gated).
export function Terminal({
  sessionKey,
  theme = DEFAULT_TERM_THEME,
}: {
  sessionKey: string;
  theme?: string;
}) {
  const hostRef = useRef<HTMLDivElement>(null);
  const termRef = useRef<Xterm | null>(null);

  // Live theme switch: xterm repaints in place, no reconnect needed.
  useEffect(() => {
    if (termRef.current)
      termRef.current.options.theme =
        TERM_THEMES[theme] ?? TERM_THEMES[DEFAULT_TERM_THEME];
  }, [theme]);

  useEffect(() => {
    const host = hostRef.current;
    if (!host) return;

    const term = new Xterm({
      // Match the attach snapshot's history depth (tmux capture -S -2000):
      // xterm's 1000-line default silently ate half the replayed history.
      scrollback: 5000,
      fontSize: 12,
      fontFamily: "ui-monospace, Menlo, monospace",
      theme: TERM_THEMES[theme] ?? TERM_THEMES[DEFAULT_TERM_THEME],
      cursorBlink: true,
      convertEol: false,
    });
    termRef.current = term;
    const fit = new FitAddon();
    term.loadAddon(fit);
    term.open(host);
    fit.fit();
    // Focus xterm's hidden input directly. term.focus() alone proved unreliable
    // on mount (after selecting an agent, focus stayed on <body>, so keystrokes
    // went nowhere and you had to click the terminal first). Targeting the
    // helper textarea after the row-click settles makes the terminal typeable
    // the moment you select an agent.
    const focusTerm = () => {
      const ta = host.querySelector<HTMLTextAreaElement>(
        ".xterm-helper-textarea",
      );
      if (ta && document.activeElement !== ta) ta.focus();
    };
    // Keep the terminal focused so you can type the moment you select an agent.
    // Two things fight us: (1) selecting an agent is a row click that settles
    // focus on <body> after this remounts; (2) xterm re-renders on every output
    // write, which blurs the helper textarea. So: focus now, and refocus whenever
    // focus leaves the terminal back to <body>/the pane (but NOT when it moves to
    // a real input, e.g. a modal — then leave it alone).
    focusTerm();
    const refocusOnBlur = (e: FocusEvent) => {
      const to = e.relatedTarget as HTMLElement | null;
      const leftToNowhere = !to || to === document.body || host.contains(to);
      if (leftToNowhere) setTimeout(focusTerm, 0);
    };
    host.addEventListener("focusout", refocusOnBlur);
    const focusOnClick = () => focusTerm();
    host.addEventListener("mousedown", focusOnClick);

    // The WS dies whenever the session's PTY goes away — Stop, a server
    // restart — and the session can come back (Resume, tmux reattach). The
    // slot stays mounted across all of that, so the terminal must reconnect
    // itself; the server repaints the current screen on each (re)attach.
    const proto = location.protocol === "https:" ? "wss" : "ws";
    let ws: WebSocket | null = null;
    let retry: number | undefined;
    let attempts = 0; // consecutive failures — drives the backoff
    let disposed = false;

    const sendResize = () => {
      if (ws?.readyState !== WebSocket.OPEN) return;
      ws.send(JSON.stringify({ resize: { cols: term.cols, rows: term.rows } }));
    };

    const connect = () => {
      ws = new WebSocket(
        `${proto}://${location.host}/sessions/${sessionKey}/terminal`,
      );
      ws.binaryType = "arraybuffer";
      ws.onopen = () => {
        attempts = 0; // live again — future retries start fast
        fit.fit();
        sendResize();
        focusTerm();
      };
      ws.onmessage = (ev) => {
        // Raw PTY bytes. xterm's write() takes a Uint8Array and decodes UTF-8
        // itself — passing bytes (not a decoded string) keeps multi-byte
        // sequences split across frames intact.
        term.write(new Uint8Array(ev.data as ArrayBuffer));
      };
      ws.onclose = () => {
        if (disposed) return;
        // Fast retries pick a Resume up promptly; exponential backoff (cap
        // 15s) keeps a long-stopped session from hammering the server with
        // 404 attaches every 1.5s forever.
        attempts += 1;
        const delay = Math.min(1500 * 1.5 ** Math.min(attempts - 1, 6), 15000);
        retry = window.setTimeout(connect, delay);
      };
    };
    connect();

    // User keystrokes -> agent stdin, verbatim (arrows, ctrl-C, partial input).
    const onData = term.onData((data) => {
      if (ws?.readyState === WebSocket.OPEN)
        ws.send(new TextEncoder().encode(data));
    });

    // Reflow the agent's TUI when the pane resizes.
    const observer = new ResizeObserver(() => {
      fit.fit();
      sendResize();
    });
    observer.observe(host);

    return () => {
      disposed = true;
      window.clearTimeout(retry);
      observer.disconnect();
      host.removeEventListener("focusout", refocusOnBlur);
      host.removeEventListener("mousedown", focusOnClick);
      onData.dispose();
      if (ws) {
        ws.onclose = null;
        ws.close();
      }
      term.dispose();
    };
    // `theme` is deliberately not a dependency: the effect above restyles the
    // live terminal in place — re-running this one would tear down the WS and
    // rebuild the terminal on every theme switch.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessionKey]);

  // height:0 + flex:1 makes the host fill the pane with a DEFINITE height, so
  // xterm scrolls its buffer internally instead of growing the page. (A
  // min-height here would let it expand and scroll the whole dashboard.)
  const bg = (TERM_THEMES[theme] ?? TERM_THEMES[DEFAULT_TERM_THEME]).background;
  return (
    <div
      ref={hostRef}
      style={{ flex: 1, height: 0, minHeight: 0, background: bg }}
    />
  );
}
