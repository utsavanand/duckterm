import { useCallback, useEffect, useState } from "react";

export const UI_FONTS = { system: { name: "System font", css: '-apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif' }, arial: { name: "Arial", css: "Arial, sans-serif" }, georgia: { name: "Georgia", css: "Georgia, serif" } };
export const TERMINAL_FONTS = { system: { name: "System monospace", css: "ui-monospace, Menlo, monospace" }, menlo: { name: "Menlo", css: "Menlo, monospace" }, monaco: { name: "Monaco", css: "Monaco, monospace" }, courier: { name: "Courier New", css: '"Courier New", monospace' } };
export interface Appearance { uiFont: keyof typeof UI_FONTS; uiSize: number; terminalFont: keyof typeof TERMINAL_FONTS; terminalSize: number }
export const DEFAULT_APPEARANCE: Appearance = { uiFont: "system", uiSize: 14, terminalFont: "system", terminalSize: 12 };
export const APPEARANCE_KEY = "rd-appearance";
const eventName = "duckterm-appearance-changed";
export function normalizeAppearance(raw: unknown): Appearance {
  const value = raw && typeof raw === "object" ? raw as Partial<Appearance> : {};
  const size = (n: unknown, fallback: number, min: number, max: number) => typeof n === "number" && Number.isFinite(n) ? Math.max(min, Math.min(max, Math.round(n))) : fallback;
  return { uiFont: value.uiFont && Object.hasOwn(UI_FONTS, value.uiFont) ? value.uiFont : "system", uiSize: size(value.uiSize, 14, 12, 20), terminalFont: value.terminalFont && Object.hasOwn(TERMINAL_FONTS, value.terminalFont) ? value.terminalFont : "system", terminalSize: size(value.terminalSize, 12, 10, 24) };
}
export function loadAppearance(): Appearance {
  try { return normalizeAppearance(JSON.parse(localStorage.getItem(APPEARANCE_KEY) ?? "null")); } catch { return { ...DEFAULT_APPEARANCE }; }
}
export function useAppearance() {
  const [appearance, setAppearance] = useState(loadAppearance);
  useEffect(() => {
    const refresh = () => setAppearance(loadAppearance());
    const storage = (event: StorageEvent) => { if (event.key === APPEARANCE_KEY || event.key === null) refresh(); };
    window.addEventListener(eventName, refresh); window.addEventListener("storage", storage);
    // Catch a change made between render and subscription.
    refresh();
    return () => { window.removeEventListener(eventName, refresh); window.removeEventListener("storage", storage); };
  }, []);
  const update = useCallback((patch: Partial<Appearance>) => {
    const next = normalizeAppearance({ ...loadAppearance(), ...patch });
    localStorage.setItem(APPEARANCE_KEY, JSON.stringify(next));
    window.dispatchEvent(new Event(eventName));
  }, []);
  return { appearance, update };
}
export function useInterfaceAppearance() {
  const value = useAppearance();
  useEffect(() => {
    document.documentElement.style.setProperty("--ui-scale", String(value.appearance.uiSize / 14));
    document.documentElement.style.setProperty("--ui-font", UI_FONTS[value.appearance.uiFont].css);
  }, [value.appearance.uiSize, value.appearance.uiFont]);
  return value;
}
