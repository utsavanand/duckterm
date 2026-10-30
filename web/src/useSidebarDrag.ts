import { useEffect, useRef, useState, type PointerEvent, type RefObject } from "react";
import { SessionView } from "./types";
import { sameSiblings } from "./sidebarOrder";

type Target = { key: string; after: boolean } | { group: string };
type Gesture = { session: SessionView; source: HTMLElement; id: number; x: number; y: number; lastX: number; lastY: number; active: boolean; target: Target | null };
export function useSidebarDrag(root: RefObject<HTMLElement>, sessions: SessionView[], reorder: (key: string, target: string, after: boolean) => void, moveGroup: (key: string, group: string) => void) {
  const gesture = useRef<Gesture | null>(null);
  const suppressed = useRef<string | null>(null);
  const [dragKey, setDragKey] = useState<string | null>(null);
  const [drop, setDrop] = useState<Target | null>(null);
  const [message, setMessage] = useState("");
  function cancel() {
    const g = gesture.current; gesture.current = null;
    if (g?.active) suppressed.current = g.session.key;
    if (g?.source.hasPointerCapture(g.id)) g.source.releasePointerCapture(g.id);
    setDragKey(null); setDrop(null);
  }
  useEffect(() => {
    const escape = (e: KeyboardEvent) => { if (e.key === "Escape" && gesture.current) { e.preventDefault(); e.stopPropagation(); cancel(); setMessage("Reorder cancelled."); } };
    const blur = () => cancel();
    document.addEventListener("keydown", escape, true); window.addEventListener("blur", blur);
    return () => { document.removeEventListener("keydown", escape, true); window.removeEventListener("blur", blur); };
  }, []);
  function locate(g: Gesture) {
    const hit = document.elementFromPoint(g.lastX, g.lastY);
    if (!hit || !root.current?.contains(hit)) return null;
    const row = hit.closest<HTMLElement>("[data-sidebar-order-key]");
    if (row) {
      const target = sessions.find(s => s.key === row.dataset.sidebarOrderKey);
      if (!target || target.key === g.session.key || !sameSiblings(g.session, target)) return null;
      const rect = row.getBoundingClientRect();
      return { key: target.key, after: g.lastY >= rect.top + rect.height / 2 };
    }
    const group = hit.closest<HTMLElement>("[data-session-drop-group]")?.dataset.sessionDropGroup;
    return group !== undefined && !g.session.parentKey && group !== (g.session.group || "") ? { group } : null;
  }
  // Continue scrolling when the pointer is held at the edge of a long sidebar.
  const tick = useRef<() => void>(() => {});
  tick.current = () => {
    const g = gesture.current;
    if (!g?.active || !root.current) return;
    const scroller = root.current.querySelector<HTMLElement>(".rd-tree");
    if (scroller) {
      const r = scroller.getBoundingClientRect();
      if (g.lastX >= r.left && g.lastX <= r.right && g.lastY >= r.top && g.lastY <= r.bottom) {
        if (g.lastY < r.top + 28) scroller.scrollTop -= 8;
        else if (g.lastY > r.bottom - 28) scroller.scrollTop += 8;
      }
    }
    g.target = locate(g); setDrop(g.target);
  };
  useEffect(() => {
    if (!dragKey) return;
    let frame: number;
    const loop = () => { tick.current(); frame = requestAnimationFrame(loop); };
    frame = requestAnimationFrame(loop);
    return () => cancelAnimationFrame(frame);
  }, [dragKey]);
  function bindings(session: SessionView) {
    return {
      onPointerDown: (e: PointerEvent<HTMLDivElement>) => {
        if (e.button !== 0 || e.isPrimary === false || (e.target as Element).closest("button,a,input,textarea,select")) return;
        suppressed.current = null;
        gesture.current = { session, source: e.currentTarget, id: e.pointerId, x: e.clientX, y: e.clientY, lastX: e.clientX, lastY: e.clientY, active: false, target: null };
      },
      onPointerMove: (e: PointerEvent<HTMLDivElement>) => {
        const g = gesture.current;
        if (!g || g.id !== e.pointerId) return;
        g.lastX = e.clientX; g.lastY = e.clientY;
        if (!g.active && Math.hypot(e.clientX - g.x, e.clientY - g.y) < 6) return;
        e.preventDefault();
        if (!g.active) { g.active = true; g.source.setPointerCapture(g.id); setDragKey(session.key); setMessage(`Moving ${session.label}. Escape cancels.`); }
        g.target = locate(g); setDrop(g.target);
      },
      onPointerUp: (e: PointerEvent<HTMLDivElement>) => {
        const g = gesture.current;
        if (!g || g.id !== e.pointerId) return;
        if (g.active) { e.preventDefault(); e.stopPropagation(); g.target = locate(g); }
        cancel();
        if (!g.active || !g.target) return;
        if ("group" in g.target) moveGroup(g.session.key, g.target.group);
        else { reorder(g.session.key, g.target.key, g.target.after); setMessage(`${session.label} reordered.`); }
      },
      onPointerCancel: () => cancel(),
      onLostPointerCapture: () => cancel(),
    };
  }
  function consumeClick(key: string, detail: number) {
    if (detail === 0 || suppressed.current !== key) return false;
    suppressed.current = null; return true;
  }
  return { bindings, consumeClick, dragKey, drop, message, announce: setMessage };
}
export type SidebarDrag = ReturnType<typeof useSidebarDrag>;
