import { useEffect, useId, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { hostName, splitSessionRef } from "./hostTransport";
import { effectiveState } from "./sessions";
import { useNow } from "./useNow";
import type { SessionView } from "./types";
import "./floatingSessionShortcuts.css";

const SESSION_PIN_LIMIT = 10;

const pinIcon = <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="m16 3 5 5-4 1-3 5 1 3-3-1-5 3-1 4-5-5 4-1 3-5-1-3 3 1z" transform="translate(1 -1) scale(.9)" /></svg>;
const initials = (label: string) => label.trim().split(/[\s_-]+/).slice(0, 2).map(part => Array.from(part)[0] || "").join("").toLocaleUpperCase() || "•";
const hostOf = (session: SessionView) => splitSessionRef(session.key).host;
const locationOf = (session: SessionView) => [session.group || "Ungrouped", session.hostLabel || hostName(session.key)].join(" · ");

function ShortcutState({ session }: { session: SessionView }) {
  const now = useNow();
  return <span className={`rd-shortcut-dot st-${session.hostOffline ? "unknown" : effectiveState(session, now)}`} aria-hidden="true" />;
}

export function FloatingSessionShortcuts({ sessions, selected, onOpen, onToggle }: {
  sessions: SessionView[]; selected: SessionView; onOpen: (key: string) => void;
  onToggle: (session: SessionView) => Promise<void>;
}) {
  const [open, setOpen] = useState(false);
  const [hovered, setHovered] = useState(false);
  const root = useRef<HTMLElement>(null);
  useEffect(() => {
    let inside = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const leave = () => {
      if (!inside || timer !== undefined) return;
      // Cross transparent gaps without flicker; their clicks still reach the terminal.
      timer = setTimeout(() => { inside = false; timer = undefined; setHovered(false); }, 180);
    };
    const move = (event: PointerEvent) => {
      if (event.target instanceof Node && root.current?.contains(event.target)) {
        clearTimeout(timer); timer = undefined; inside = true; setHovered(true);
      } else leave();
    };
    // Observe actual DOM targets: the picker is portaled outside this floating rail.
    document.addEventListener("pointermove", move);
    document.addEventListener("pointerleave", leave);
    window.addEventListener("blur", leave);
    return () => {
      clearTimeout(timer);
      document.removeEventListener("pointermove", move);
      document.removeEventListener("pointerleave", leave);
      window.removeEventListener("blur", leave);
    };
  }, []);
  const [query, setQuery] = useState("");
  const [pending, setPending] = useState<string | null>(null);
  const [error, setError] = useState("");
  const add = useRef<HTMLButtonElement>(null);
  const picker = useRef<HTMLDivElement>(null);
  const search = useRef<HTMLInputElement>(null);
  const id = useId();
  const [position, setPosition] = useState({ left: 8, top: 8, width: 310, maxHeight: 400 });
  const pins = sessions.filter(s => s.pinned);
  const atCapacity = (s: SessionView) => pins.filter(p => hostOf(p) === hostOf(s)).length >= SESSION_PIN_LIMIT;
  const visible = sessions.filter(s => s.pinned || !["archived", "merged"].includes(s.state))
    .filter(s => `${s.label} ${locationOf(s)}`.toLocaleLowerCase().includes(query.toLocaleLowerCase()));

  useLayoutEffect(() => {
    if (!open) return;
    const place = () => {
      const rect = add.current?.getBoundingClientRect();
      if (!rect) return;
      const width = Math.min(310, window.innerWidth - 16);
      const below = window.innerHeight - rect.bottom - 14;
      const above = rect.top - 14;
      const maxHeight = Math.max(40, Math.min(420, Math.max(below, above)));
      const height = Math.min(picker.current?.scrollHeight ?? 420, maxHeight);
      setPosition({ width, maxHeight, left: Math.max(8, Math.min(rect.right - width, window.innerWidth - width - 8)),
        top: below >= height ? rect.bottom + 6 : Math.max(8, rect.top - height - 6) });
    };
    place();
    window.addEventListener("resize", place);
    window.addEventListener("scroll", place, true);
    return () => { window.removeEventListener("resize", place); window.removeEventListener("scroll", place, true); };
  }, [open, query, sessions]);
  useEffect(() => {
    if (!open) return;
    search.current?.focus();
    const outside = (event: PointerEvent) => {
      if (event.target instanceof Node && !picker.current?.contains(event.target) && !add.current?.contains(event.target)) setOpen(false);
    };
    const escape = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      event.preventDefault(); event.stopPropagation();
      setOpen(false); add.current?.focus({ preventScroll: true });
    };
    document.addEventListener("pointerdown", outside);
    document.addEventListener("keydown", escape, true);
    return () => {
      document.removeEventListener("pointerdown", outside);
      document.removeEventListener("keydown", escape, true);
    };
  }, [open]);

  function closePicker() { setOpen(false); add.current?.focus({ preventScroll: true }); }
  async function toggle(session: SessionView) {
    if (pending) return;
    setPending(session.key); setError("");
    try {
      await onToggle(session);
      // Unpinning removes its floating button, so restore a reachable focus target.
      if (!open) add.current?.focus({ preventScroll: true });
      else search.current?.focus({ preventScroll: true });
    } catch (e) { setError((e as Error).message); }
    finally { setPending(null); }
  }
  function pinButton(session: SessionView, place: string) {
    const capped = !session.pinned && atCapacity(session);
    const offline = !!session.hostOffline;
    return <button className="rd-shortcut-pin" aria-label={`${session.pinned ? "Unpin" : "Pin"} ${session.label} ${place}`}
      aria-pressed={!!session.pinned} disabled={!!pending || capped || offline}
      title={offline ? "Reconnect this host to change its pins" : capped ? `Unpin a session on this host first (${SESSION_PIN_LIMIT} pinned)` : `${session.pinned ? "Unpin" : "Pin"} ${session.label}`}
      onClick={() => void toggle(session)}>{pinIcon}</button>;
  }
  return <nav ref={root} className={`rd-floating-shortcuts${hovered ? " is-hovered" : ""}${open ? " picker-open" : ""}`} aria-label="Pinned session shortcuts">
    <div className="rd-shortcut-heading"><span>Pinned</span><button ref={add} aria-label="Choose sessions to pin" aria-expanded={open} aria-controls={id}
      title="Choose sessions to pin" onClick={() => { setQuery(""); setOpen(!open); }}>+</button></div>
    <div className="rd-shortcut-list">{pins.map(s => <div className={`rd-shortcut-tab${s.key === selected.key ? " active" : ""}`} key={s.key}>
      <button className="rd-shortcut-open" aria-label={`Open pinned ${s.label}`} aria-current={s.key === selected.key ? "true" : undefined}
        title={`${s.label} · ${locationOf(s)}${s.hostOffline ? " · Offline" : ""}`} onClick={() => onOpen(s.key)}>
        <span className="rd-shortcut-name">{s.label}</span>
        <span className="rd-shortcut-badge" aria-hidden="true">{initials(s.label)}<ShortcutState session={s} /></span>
      </button>{pinButton(s, "from shortcuts")}
    </div>)}</div>
    {!selected.pinned && <button className="rd-shortcut-current" disabled={!!pending || atCapacity(selected) || !!selected.hostOffline}
      title={selected.hostOffline ? "Reconnect this host to pin its session" : atCapacity(selected) ? `Unpin a session on this host first (${SESSION_PIN_LIMIT} pinned)` : `Pin ${selected.label}`}
      aria-label={`Pin current session ${selected.label}`} onClick={() => void toggle(selected)}><span>Pin {selected.label}</span>{pinIcon}</button>}
    {!pins.length && <p className="rd-shortcut-empty">Pin a session to keep it here.</p>}
    {error && <p role="alert" className="rd-shortcut-error">{error}</p>}
    {open && createPortal(<div ref={picker} id={id} className="rd-shortcut-picker" role="dialog" aria-label="Pin sessions" style={position}
      onBlur={e => { if (e.relatedTarget instanceof Node && !e.currentTarget.contains(e.relatedTarget) && !add.current?.contains(e.relatedTarget)) setOpen(false); }}>
      <header><span>Pin sessions</span><button aria-label="Close pin picker" onClick={closePicker}>×</button></header>
      <p>Up to {SESSION_PIN_LIMIT} sessions per host. Shared with the sidebar and Focus.</p>
      <input ref={search} type="search" aria-label="Find a session" placeholder="Find a session…" value={query} onChange={e => setQuery(e.target.value)} />
      <div className="rd-shortcut-choices">{visible.map(s => <div className="rd-shortcut-choice" key={s.key}>
        <div><span>{s.label}</span><small>{locationOf(s)}{s.key === selected.key ? " · Current session" : ""}{s.hostOffline ? " · Offline" : ""}</small></div>
        {pinButton(s, "in picker")}
      </div>)}{!visible.length && <p>No sessions found.</p>}</div>
    </div>, document.body)}
  </nav>;
}
