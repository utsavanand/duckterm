import { useCallback, useEffect, useState } from "react";

export type TextContrast = "readable" | "preserve";
type Preferences = { sessions: Record<string, TextContrast>; folders: Record<string, TextContrast> };
export const CONTRAST_KEY = "rd-terminal-contrast";
const changed = "duckterm-terminal-contrast";
function valid(value: unknown): value is TextContrast { return value === "readable" || value === "preserve"; }
export function loadContrast(): Preferences {
  try {
    const raw = JSON.parse(localStorage.getItem(CONTRAST_KEY) ?? "null");
    const picks = (value: unknown): Record<string, TextContrast> => value && typeof value === "object" && !Array.isArray(value)
      ? Object.fromEntries(Object.entries(value).filter(([, v]) => valid(v))) : {};
    return { sessions: picks(raw?.sessions), folders: picks(raw?.folders) };
  } catch { return { sessions: {}, folders: {} }; }
}
export function resolveContrast(preferences: Preferences, sessionKey: string, folder?: string): TextContrast {
  if (Object.hasOwn(preferences.sessions, sessionKey)) return preferences.sessions[sessionKey];
  let path = folder ?? "";
  while (path) {
    if (Object.hasOwn(preferences.folders, path)) return preferences.folders[path];
    path = path.includes("/") ? path.slice(0, path.lastIndexOf("/")) : "";
  }
  return "readable";
}
export function useTerminalContrast() {
  const [preferences, setPreferences] = useState(loadContrast);
  useEffect(() => {
    const refresh = () => setPreferences(loadContrast());
    const storage = (event: StorageEvent) => { if (event.key === CONTRAST_KEY || event.key === null) refresh(); };
    window.addEventListener(changed, refresh); window.addEventListener("storage", storage); refresh();
    return () => { window.removeEventListener(changed, refresh); window.removeEventListener("storage", storage); };
  }, []);
  const update = useCallback((scope: keyof Preferences, key: string, value: TextContrast | null) => {
    const next = loadContrast(), picks = { ...next[scope] };
    if (value) picks[key] = value; else delete picks[key];
    localStorage.setItem(CONTRAST_KEY, JSON.stringify({ ...next, [scope]: picks }));
    window.dispatchEvent(new Event(changed));
  }, []);
  return { preferences, update };
}
