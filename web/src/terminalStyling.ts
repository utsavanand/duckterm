import { useCallback, useEffect, useState } from "react";

export type TextStyling = "enhanced" | "program";
type Preferences = { sessions: Record<string, TextStyling>; folders: Record<string, TextStyling> };
export const STYLING_KEY = "rd-terminal-styling";
const changed = "duckterm-terminal-styling";
function valid(value: unknown): value is TextStyling { return value === "enhanced" || value === "program"; }
export function loadStyling(): Preferences {
  try {
    const raw = JSON.parse(localStorage.getItem(STYLING_KEY) ?? "null");
    const picks = (value: unknown): Record<string, TextStyling> => value && typeof value === "object" && !Array.isArray(value)
      ? Object.fromEntries(Object.entries(value).filter(([, v]) => valid(v))) : {};
    return { sessions: picks(raw?.sessions), folders: picks(raw?.folders) };
  } catch { return { sessions: {}, folders: {} }; }
}
export function resolveStyling(preferences: Preferences, sessionKey: string, folder?: string): TextStyling {
  if (Object.hasOwn(preferences.sessions, sessionKey)) return preferences.sessions[sessionKey];
  let path = folder ?? "";
  while (path) {
    if (Object.hasOwn(preferences.folders, path)) return preferences.folders[path];
    path = path.includes("/") ? path.slice(0, path.lastIndexOf("/")) : "";
  }
  return "enhanced";
}
export function useTerminalStyling() {
  const [preferences, setPreferences] = useState(loadStyling);
  useEffect(() => {
    const refresh = () => setPreferences(loadStyling());
    const storage = (event: StorageEvent) => { if (event.key === STYLING_KEY || event.key === null) refresh(); };
    window.addEventListener(changed, refresh); window.addEventListener("storage", storage); refresh();
    return () => { window.removeEventListener(changed, refresh); window.removeEventListener("storage", storage); };
  }, []);
  const update = useCallback((scope: keyof Preferences, key: string, value: TextStyling | null) => {
    const next = loadStyling(), picks = { ...next[scope] };
    if (value) picks[key] = value; else delete picks[key];
    localStorage.setItem(STYLING_KEY, JSON.stringify({ ...next, [scope]: picks }));
    window.dispatchEvent(new Event(changed));
  }, []);
  return { preferences, update };
}
