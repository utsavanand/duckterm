import { useEffect, useRef } from "react";
import { Terminal as Xterm } from "@xterm/xterm";
import { FitAddon } from "@xterm/addon-fit";
import "@xterm/xterm/css/xterm.css";
import { authHeaders } from "./api";
import { bindClipboardBridge, imagePathText, releaseClipboardBridge } from "./clipboardBridge";
import { useToast } from "./ui";
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
  active = true,
  theme = DEFAULT_TERM_THEME,
}: {
  sessionKey: string;
  active?: boolean;
  theme?: string;
}) {
  const toast = useToast();
  const toastRef = useRef(toast);
  toastRef.current = toast;
  const hostRef = useRef<HTMLDivElement>(null);
  const termRef = useRef<Xterm | null>(null);
  const activateRef = useRef<((visible: boolean) => void) | null>(null);

  // Live theme switch (e.g. toggling the app light/dark): set the new palette
  // and force a full repaint so already-rendered rows recolor immediately,
  // not just on the next write.
  useEffect(() => {
    const term = termRef.current;
    if (!term) return;
    term.options.theme = TERM_THEMES[theme] ?? TERM_THEMES[DEFAULT_TERM_THEME];
    term.refresh(0, term.rows - 1);
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
    let visible = active;
    if (visible) fit.fit();
    bindClipboardBridge(term, sessionKey); // Mac-app Edit menu targets the focused terminal
    // Focus xterm's hidden input directly. term.focus() alone proved unreliable
    // on mount (after selecting an agent, focus stayed on <body>, so keystrokes
    // went nowhere and you had to click the terminal first). Targeting the
    // helper textarea after the row-click settles makes the terminal typeable
    // the moment you select an agent.
    const focusTerm = (explicit = false) => {
      // Async attach/replay must not steal focus from a menu or dialog opened
      // while the terminal was connecting. Explicit terminal selection can.
      const focused = document.activeElement;
      if (!explicit && focused && focused !== document.body && !host.contains(focused)) return;
      const ta = host.querySelector<HTMLTextAreaElement>(
        ".xterm-helper-textarea",
      );
      if (visible && ta && document.activeElement !== ta) ta.focus({ preventScroll: true });
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
    const focusOnClick = () => focusTerm(true);
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
    let attachGeneration = 0;
    let replayReady = false;
    let pendingOpenScroll = false;
    let openGeneration = 0;
    let scrollFrame: number | undefined;
    const cancelAttachScroll = () => {
      pendingOpenScroll = false;
      ++openGeneration;
      window.cancelAnimationFrame(scrollFrame ?? 0);
    };
    const cancelForNavigation = (event: KeyboardEvent) => {
      if (["PageUp", "PageDown", "Home", "End", "ArrowUp", "ArrowDown"].includes(event.key)) cancelAttachScroll();
    };
    host.addEventListener("wheel", cancelAttachScroll, { passive: true });
    host.addEventListener("pointerdown", cancelAttachScroll);
    host.addEventListener("touchstart", cancelAttachScroll, { passive: true });
    host.addEventListener("keydown", cancelForNavigation);


    const sendResize = () => {
      if (ws?.readyState !== WebSocket.OPEN) return;
      ws.send(JSON.stringify({ resize: { cols: term.cols, rows: term.rows } }));
    };

    // Hidden terminals have no usable dimensions. Fit only after activation,
    // then wait for queued parser work and the browser layout before scrolling.
    // Ordinary output and visible resizes must not interrupt scrollback reading.
    const settleOpening = () => {
      if (!visible || !host.clientWidth || !host.clientHeight) return;
      fit.fit();
      sendResize();
      if (!pendingOpenScroll || !replayReady) return;
      const generation = ++openGeneration;
      term.write("", () => {
        if (disposed || generation !== openGeneration) return;
        window.cancelAnimationFrame(scrollFrame ?? 0);
        scrollFrame = window.requestAnimationFrame(() => {
          if (disposed || !visible || !pendingOpenScroll || generation !== openGeneration) return;
          term.scrollToBottom();
          // xterm synchronizes its DOM viewport on its next render frame.
          // Finish after that frame so a hidden-pane reflow cannot restore an
          // older scrollbar position after our first scroll.
          scrollFrame = window.requestAnimationFrame(() => {
            if (disposed || !visible || !pendingOpenScroll || generation !== openGeneration) return;
            // At the buffer bottom xterm skips its scroll event, even when
            // the DOM scrollbar still holds the pre-reflow height. Moving one
            // line and back in this frame forces its public scroll API to sync.
            term.scrollLines(-1);
            term.scrollToBottom();
            pendingOpenScroll = false;
            focusTerm();
          });
        });
      });
    };
    activateRef.current = (nextVisible) => {
      visible = nextVisible;
      cancelAttachScroll();
      pendingOpenScroll = visible;
      if (visible) { settleOpening(); focusTerm(true); }
    };

    const connect = () => {
      const generation = ++attachGeneration;
      let firstFrame = true;
      replayReady = false;
      cancelAttachScroll();
      pendingOpenScroll = visible;
      ws = new WebSocket(
        `${proto}://${location.host}/sessions/${sessionKey}/terminal`,
      );
      ws.binaryType = "arraybuffer";
      ws.onopen = () => {
        attempts = 0; // live again — future retries start fast
        settleOpening();
        focusTerm();
      };
      ws.onmessage = (ev) => {
        // Raw PTY bytes. xterm's write() takes a Uint8Array and decodes UTF-8
        // itself — passing bytes (not a decoded string) keeps multi-byte
        // sequences split across frames intact.
        if (firstFrame) {
          firstFrame = false;
          // subscribe_bytes sends the complete attach snapshot as its first
          // frame. Wait for xterm's parser, not a timer or later live writes.
          term.write(new Uint8Array(ev.data as ArrayBuffer), () => {
            if (disposed || generation !== attachGeneration) return;
            replayReady = true;
            settleOpening();
          });
        } else {
          term.write(new Uint8Array(ev.data as ArrayBuffer));
        }
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

    // Capture image paste before xterm's normal text handler. Many clipboard
    // entries carry both image bytes and a filename; only send the saved image.
    const onPasteImage = (event: ClipboardEvent) => {
      const image = [...(event.clipboardData?.items ?? [])].find((item) => item.kind === "file" && item.type.startsWith("image/"));
      if (!image) return;
      event.preventDefault();
      event.stopImmediatePropagation();
      const blob = image.getAsFile();
      const socket = ws;
      if (!blob || !socket || socket.readyState !== WebSocket.OPEN) {
        toastRef.current("Image paste failed: select a connected terminal and try again.", "err");
        return;
      }
      void fetch("/paste-image", {
        method: "POST", headers: authHeaders({ "Content-Type": blob.type }), body: blob,
      }).then(async (response) => {
        const data = await response.json() as { path?: string; error?: string };
        if (!response.ok || !data.path) throw new Error(data.error ?? "Could not save clipboard image");
        if (disposed || ws !== socket || socket.readyState !== WebSocket.OPEN) throw new Error("Terminal reconnected. Paste the image again.");
        term.paste(imagePathText(data.path));
      }).catch((error: Error) => toastRef.current(`Image paste failed: ${error.message}`, "err"));
    };
    host.addEventListener("paste", onPasteImage, true);

    // Shift+Enter inserts a newline instead of submitting. xterm would send
    // plain \r for it — indistinguishable from Enter — so intercept and send
    // LF (Ctrl+J), the newline keystroke both claude-code and codex accept.
    // preventDefault too: xterm skips a suppressed key, but the browser's own
    // default would still insert a newline into the hidden helper textarea.
    term.attachCustomKeyEventHandler((e) => {
      if (e.type === "keydown" && e.key === "Enter" && e.shiftKey) {
        e.preventDefault();
        if (ws?.readyState === WebSocket.OPEN) ws.send(new Uint8Array([0x0a]));
        return false;
      }
      return true;
    });

    // Reflow the agent's TUI when the pane resizes.
    const observer = new ResizeObserver(settleOpening);
    observer.observe(host);

    return () => {
      disposed = true;
      activateRef.current = null;
      cancelAttachScroll();
      window.clearTimeout(retry);
      observer.disconnect();
      host.removeEventListener("focusout", refocusOnBlur);
      host.removeEventListener("mousedown", focusOnClick);
      host.removeEventListener("wheel", cancelAttachScroll);
      host.removeEventListener("pointerdown", cancelAttachScroll);
      host.removeEventListener("touchstart", cancelAttachScroll);
      host.removeEventListener("keydown", cancelForNavigation);
      host.removeEventListener("paste", onPasteImage, true);
      onData.dispose();
      if (ws) {
        ws.onclose = null;
        ws.close();
      }
      releaseClipboardBridge(term);
      term.dispose();
    };
    // `theme` is deliberately not a dependency: the effect above restyles the
    // live terminal in place — re-running this one would tear down the WS and
    // rebuild the terminal on every theme switch.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessionKey]);

  useEffect(() => { activateRef.current?.(active); }, [active, sessionKey]);

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
