import { useEffect, useRef, useState } from "react";
import { SessionView } from "./types";

export const SIDEBAR_ORDER_KEY = "rd.sidebarSessionOrder.v1";
const changed = "duckterm-sidebar-order";
export function readSidebarOrder(): string[] {
  try {
    const value: unknown = JSON.parse(localStorage.getItem(SIDEBAR_ORDER_KEY) || "[]");
    return Array.isArray(value) ? [...new Set(value.filter((key): key is string => typeof key === "string" && key.length > 0 && key.length <= 1024))] : [];
  } catch { return []; }
}
export function orderSessions<T extends { key: string }>(sessions: T[], order: string[]): T[] {
  const rank = new Map(order.map((key, index) => [key, index]));
  return [...sessions].sort((a, b) => (rank.get(a.key) ?? Infinity) - (rank.get(b.key) ?? Infinity));
}
export function sameSiblings(a: SessionView, b: SessionView): boolean {
  return (a.parentKey || "") === (b.parentKey || "") && (a.group || "") === (b.group || "");
}
export function moveInOrder(order: string[], key: string, target: string, after: boolean): string[] {
  if (key === target || !order.includes(key) || !order.includes(target)) return order;
  const next = order.filter(k => k !== key);
  next.splice(next.indexOf(target) + Number(after), 0, key);
  return next;
}
// Session keys are already host-qualified. Keep missing keys: a disconnected host
// or a filtered view must not erase its order. New sessions append, never reshuffle.
export function useSidebarOrder(sessions: SessionView[]) {
  const [order, setOrder] = useState(readSidebarOrder);
  const latest = useRef(order);
  const [error, setError] = useState("");
  function save(next: string[]) {
    latest.current = next; setOrder(next);
    try {
      localStorage.setItem(SIDEBAR_ORDER_KEY, JSON.stringify(next)); setError("");
      window.dispatchEvent(new Event(changed));
    } catch { setError("Session order changed for now, but could not be saved on this device."); }
  }
  useEffect(() => {
    const reload = (event: Event) => {
      if (event instanceof StorageEvent && event.key !== null && event.key !== SIDEBAR_ORDER_KEY) return;
      latest.current = readSidebarOrder(); setOrder(latest.current);
    };
    window.addEventListener("storage", reload); window.addEventListener(changed, reload);
    return () => { window.removeEventListener("storage", reload); window.removeEventListener(changed, reload); };
  }, []);
  useEffect(() => {
    const known = new Set(latest.current);
    const added = sessions.filter(s => !known.has(s.key)).map(s => s.key);
    if (added.length) save([...latest.current, ...added]);
  }, [sessions]);
  function reorder(key: string, target: string, after: boolean) {
    const a = sessions.find(s => s.key === key), b = sessions.find(s => s.key === target);
    if (!a || !b || !sameSiblings(a, b)) return;
    save(moveInOrder(latest.current, key, target, after));
  }
  return { sessions: orderSessions(sessions, order), reorder, error };
}
