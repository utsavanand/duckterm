import { useEffect, useRef, useState } from "react";
import { splitSessionRef } from "./hostTransport";

export const SHORTCUT_COLORS = { None: "", Red: "#ed7878", Orange: "#eca763", Yellow: "#dfc45a", Green: "#65bb92", Blue: "#77a9e4", Purple: "#b396e2", Pink: "#dc92bb" } as const;
export type ShortcutColor = keyof typeof SHORTCUT_COLORS;
export type ShortcutPreferences = { display: "hover" | "expanded"; colors: Record<string, ShortcutColor>; order: string[] };
export const SHORTCUT_PREFERENCES_KEY = "rd-pinned-shortcut-preferences-v1";
const changeEvent = "duckterm-shortcut-preferences";
const defaults = (): ShortcutPreferences => ({ display: "hover", colors: {}, order: [] });

export function readShortcutPreferences(): ShortcutPreferences {
  try {
    const value: unknown = JSON.parse(localStorage.getItem(SHORTCUT_PREFERENCES_KEY) || "null");
    if (!value || typeof value !== "object") return defaults();
    const { display, colors, order } = value as Partial<ShortcutPreferences>;
    return {
      display: display === "expanded" ? "expanded" : "hover",
      colors: colors && typeof colors === "object" && !Array.isArray(colors) ? Object.fromEntries(Object.entries(colors)
        .filter(([key, color]) => key.length <= 1024 && Object.hasOwn(SHORTCUT_COLORS, color)).slice(0, 2000)) : {},
      order: Array.isArray(order) ? [...new Set(order.filter(key => typeof key === "string" && key.length <= 1024))].slice(0, 2000) : [],
    };
  } catch { return defaults(); }
}

// Keys are host-qualified session identities, never mutable labels or folders.
// Only presentation is local to this client; pin membership remains server-owned.
export function useShortcutPreferences() {
  const [preferences, setPreferences] = useState(readShortcutPreferences);
  const latest = useRef(preferences);
  const [storageError, setStorageError] = useState("");
  useEffect(() => {
    const reload = (event: Event) => {
      if (event instanceof StorageEvent && event.key !== null && event.key !== SHORTCUT_PREFERENCES_KEY) return;
      latest.current = readShortcutPreferences(); setPreferences(latest.current);
    };
    window.addEventListener("storage", reload); window.addEventListener(changeEvent, reload);
    return () => { window.removeEventListener("storage", reload); window.removeEventListener(changeEvent, reload); };
  }, []);
  function update(change: (previous: ShortcutPreferences) => ShortcutPreferences) {
    const next = change(latest.current);
    latest.current = next; setPreferences(next);
    try {
      localStorage.setItem(SHORTCUT_PREFERENCES_KEY, JSON.stringify(next)); setStorageError("");
      window.dispatchEvent(new Event(changeEvent));
    } catch { setStorageError("Shortcut preferences changed for now, but could not be saved on this device."); }
  }
  return { preferences, update, storageError };
}

export function orderShortcuts<T extends { key: string; group?: string | null }>(sessions: T[], order: string[]): T[] {
  const positions = new Map(order.map((key, index) => [key, index]));
  const ordered = [...sessions].sort((a, b) => (positions.get(a.key) ?? Infinity) - (positions.get(b.key) ?? Infinity));
  const group = (s: T) => JSON.stringify([splitSessionRef(s.key).host, s.group || ""]);
  // Focus uses the same top-three order as the grouped floating rail.
  return [...new Set(ordered.map(group))].flatMap(key => ordered.filter(s => group(s) === key));
}
