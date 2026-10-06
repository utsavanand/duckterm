import { useCallback, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { authHeaders } from "./api";
import { hostName, routedFetch, splitSessionRef } from "./hostTransport";
import { Terminal } from "./Terminal";
import type { SessionView } from "./types";
import "./sessionShell.css";

type ShellStatus = { open: boolean; pane_id?: string; foreground?: string | null; confirmation_required: boolean; confirmation_token?: string; closed?: boolean };
type Preference = { expanded: boolean; height: number };
const storageKey = (key: string) => `rd.sessionShell.${key}`;
function load(key: string): Preference {
  try {
    const value = JSON.parse(localStorage.getItem(storageKey(key)) ?? "null");
    return { expanded: value?.expanded === true, height: typeof value?.height === "number" && Number.isFinite(value.height) ? Math.max(130, Math.min(800, value.height)) : 230 };
  } catch { return { expanded: false, height: 230 }; }
}
export function SessionShell({ session, active, theme }: { session: SessionView; active: boolean; theme: string }) {
  const key = session.key;
  const [preference, setPreference] = useState(() => load(key));
  const [status, setStatus] = useState<ShellStatus | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [connected, setConnected] = useState(false);
  const [confirmation, setConfirmation] = useState<ShellStatus | null>(null);
  const [changedJob, setChangedJob] = useState(false);
  const [portal, setPortal] = useState<HTMLElement | null>(null);
  const [maxHeight, setMaxHeight] = useState(500);
  const root = useRef<HTMLDivElement>(null), dialog = useRef<HTMLDialogElement>(null), toggle = useRef<HTMLButtonElement>(null);
  const sequence = useRef(0), alive = useRef(true), acting = useRef(false);
  const invalidate = useCallback(() => { ++sequence.current; }, []);
  const supported = !!session.ptyOwned && !["archived", "merged"].includes(session.state);
  const offline = !!session.hostOffline;
  const unavailable = !supported ? "This agent isn’t running in a terminal DuckTerm owns. A companion shell is available for sessions with a DuckTerm-owned terminal." : offline ? `Can’t reach ${hostName(key)}. Reconnect to use the shell on that host.` : error;
  const remote = splitSessionRef(key).host !== "local";
  const path = `/sessions/${encodeURIComponent(key)}/shell`;
  const remember = useCallback((patch: Partial<Preference>) => {
    setPreference(old => {
      const next = { ...old, ...patch };
      try { localStorage.setItem(storageKey(key), JSON.stringify(next)); } catch { /* The pane still works without storage. */ }
      return next;
    });
  }, [key]);
  useEffect(() => { alive.current = true; setPortal(document.getElementById("rd-shell-toggle")); return () => { alive.current = false; invalidate(); }; }, [invalidate]);
  const refresh = useCallback(async () => {
    if (!supported || offline || acting.current) return;
    const stamp = ++sequence.current;
    try {
      const response = await routedFetch(path, { headers: authHeaders(), cache: "no-store" });
      const next = await response.json();
      if (!response.ok) throw new Error(next.error ?? "Shell unavailable on this host.");
      if (typeof next.open !== "boolean") throw new Error("This host returned an invalid shell status.");
      if (!alive.current || stamp !== sequence.current) return;
      setStatus(next); setError("");
      if (!next.open) { remember({ expanded: false }); setConfirmation(null); }
    } catch (e) { if (alive.current && stamp === sequence.current) setError((e as Error).message); }
  }, [supported, offline, path, remember]);
  useEffect(() => {
    if (!active) return;
    void refresh();
    const interval = window.setInterval(() => void refresh(), 3000);
    return () => { window.clearInterval(interval); invalidate(); };
  }, [active, refresh, invalidate]);
  useEffect(() => {
    const parent = root.current?.parentElement;
    if (!parent) return;
    const measure = () => { if (parent.clientHeight) setMaxHeight(Math.max(130, parent.clientHeight - 260)); };
    const observer = new ResizeObserver(measure); observer.observe(parent); measure();
    return () => observer.disconnect();
  }, []);
  useEffect(() => {
    if (confirmation && active && !offline) { if (!dialog.current?.open) dialog.current?.showModal(); }
    else if (dialog.current?.open) dialog.current.close();
  }, [confirmation, active, offline]);
  async function open() {
    remember({ expanded: true });
    if (!supported || offline || acting.current) return;
    if (status?.open) { void refresh(); return; }
    acting.current = true; ++sequence.current; setBusy(true); setError("");
    try {
      const response = await routedFetch(path, { method: "POST", headers: authHeaders({ "Content-Type": "application/json" }), body: "{}" });
      const next = await response.json();
      if (!response.ok) throw new Error(next.error ?? "Could not open shell.");
      if (!alive.current) return;
      setStatus(next);
    } catch (e) { if (alive.current) setError((e as Error).message); }
    finally { acting.current = false; if (alive.current) setBusy(false); }
  }
  function collapse() { setConnected(false); remember({ expanded: false }); setConfirmation(null); toggle.current?.focus(); }
  async function close(expected?: string) {
    if (acting.current || offline) return;
    acting.current = true; ++sequence.current; setBusy(true); setError("");
    try {
      const response = await routedFetch(path, { method: "DELETE", headers: authHeaders({ "Content-Type": "application/json" }), body: JSON.stringify(expected ? { force: true, confirmation_token: expected } : {}) });
      const next = await response.json();
      if (!alive.current) return;
      if (response.status === 409 && next.confirmation_required && next.confirmation_token) {
        setStatus(next); setConfirmation(next); setChangedJob(!!expected); return;
      }
      if (!response.ok) throw new Error(next.error ?? "Could not close shell.");
      setStatus(next); setConfirmation(null); remember({ expanded: false }); toggle.current?.focus();
    } catch (e) { if (alive.current) setError((e as Error).message); }
    finally { acting.current = false; if (alive.current) setBusy(false); }
  }
  const expanded = preference.expanded;
  const show = active && (expanded || !!status?.open);
  const process = status?.confirmation_required ? status.foreground || "Unknown process" : "Idle";
  const height = Math.min(preference.height, maxHeight);
  const heading = remote ? `☁ ${hostName(key)} · ${session.worktreePath || session.cwd || "Project folder"}` : session.worktreePath || session.cwd || "Project folder";
  return <>
    {portal && active && createPortal(<button ref={toggle} className={expanded ? "active" : ""} aria-expanded={expanded} aria-controls="rd-session-shell" disabled={busy} onClick={() => expanded ? collapse() : void open()}>›_ Shell{!expanded && status?.open ? unavailable ? " · Unavailable" : status.confirmation_required ? " · Running" : " · Idle" : ""}</button>, portal)}
    <div ref={root} className="rd-shell-root" style={{ display: show ? "flex" : "none" }}>
      {expanded ? <>
        <div className="rd-shell-divider" role="separator" aria-label="Resize shell" aria-orientation="horizontal" aria-valuemin={130} aria-valuemax={maxHeight} aria-valuenow={height} tabIndex={0}
          onKeyDown={e => { if (!["ArrowUp", "ArrowDown", "Home", "End"].includes(e.key)) return; e.preventDefault(); remember({ height: e.key === "Home" ? 130 : e.key === "End" ? maxHeight : Math.max(130, Math.min(maxHeight, height + (e.key === "ArrowUp" ? 24 : -24))) }); }}
          onPointerDown={e => {
            if (e.button !== 0) return;
            const bar = e.currentTarget, start = e.clientY, initial = height;
            bar.setPointerCapture(e.pointerId);
            const move = (event: PointerEvent) => remember({ height: Math.max(130, Math.min(maxHeight, initial + start - event.clientY)) });
            const done = () => { bar.removeEventListener("pointermove", move); bar.removeEventListener("pointerup", done); bar.removeEventListener("pointercancel", done); };
            bar.addEventListener("pointermove", move); bar.addEventListener("pointerup", done); bar.addEventListener("pointercancel", done);
          }} />
        <section id="rd-session-shell" className="rd-session-shell" aria-label="Session shell" style={{ height }}>
          <header><strong>›_ Shell</strong><span className="rd-shell-path" title={heading}>{heading}</span><span className={`rd-shell-state${unavailable ? " unavailable" : ""}`}>{unavailable ? "Unavailable" : busy ? "Working…" : status?.open ? connected ? process : "Connecting…" : "Checking…"}</span><div className="rd-shell-actions"><button onClick={collapse}>⌄ Collapse</button><button aria-label="Close shell" title="Close shell" disabled={busy || !!unavailable || !status?.open} onClick={() => void close()}>×</button></div></header>
          {unavailable ? <div className="rd-shell-unavailable"><strong>{supported ? "Shell unavailable" : "Shell not supported for this session"}</strong><p>{unavailable}</p>{remote && <p>Commands stay on the owning host.</p>}{supported && <button disabled={busy} onClick={() => void refresh()}>Retry connection</button>}</div>
            : active && status?.open ? <Terminal key={status.pane_id} sessionKey={key} kind="shell" active theme={theme} onConnection={setConnected} />
              : <p className="rd-shell-loading" role="status">{busy ? "Opening shell…" : "Checking shell…"}</p>}
        </section>
      </> : <div className="rd-shell-collapsed"><strong>›_ Shell</strong><span className={unavailable ? "unavailable" : ""}>{unavailable ? "Unavailable" : process}</span><small>{unavailable ? "Reconnect to continue" : status?.confirmation_required ? "Keeps running" : "Ready when you return"}</small><button onClick={() => void open()}>Expand</button><button aria-label="Close collapsed shell" disabled={busy || !!unavailable} onClick={() => void close()}>×</button></div>}
    </div>
    {createPortal(<dialog ref={dialog} className="rd-shell-confirm" aria-labelledby="rd-shell-confirm-title" onCancel={e => { if (busy) e.preventDefault(); else setConfirmation(null); }} onClose={() => { setConfirmation(null); toggle.current?.focus(); }}>
      <h2 id="rd-shell-confirm-title">Close this shell?</h2><p>{changedJob ? "The running process changed. Review it before closing." : "A command may still be running in this shell."}</p>
      <div className="rd-shell-process"><code>{confirmation?.foreground || "Unknown process"}</code><small>{session.label} · {hostName(key)}</small></div>
      <p>Closing will stop the command and end the shell. The agent terminal will keep running.</p><p>To hide the pane and leave the command running, choose Collapse.</p>
      {error && <p role="alert">{error}</p>}
      <footer><button disabled={busy} autoFocus onClick={() => setConfirmation(null)}>Keep open</button><button disabled={busy} onClick={collapse}>Collapse</button><button className="rd-shell-danger" disabled={busy || offline || !confirmation?.confirmation_token} onClick={() => void close(confirmation?.confirmation_token)}>{busy ? "Closing…" : "Close shell"}</button></footer>
    </dialog>, document.body)}
  </>;
}
