import { useEffect, useRef, useState } from "react";
import { splitSessionRef } from "./hostTransport";

const STORAGE_KEY = "rd.notifyOn";
const permissionNow = () => "Notification" in window ? Notification.permission : "unavailable";
interface NoticeSession { key: string; label: string; waiting: boolean; }
export function useDesktopNotifications(sessions: NoticeSession[], loadedHosts: string[]) {
  const [permission, setPermission] = useState(permissionNow);
  const [enabled, setEnabled] = useState(() => {
    try { const saved = localStorage.getItem(STORAGE_KEY); return saved === null ? permissionNow() === "granted" : saved === "true"; }
    catch { return permissionNow() === "granted"; }
  });
  const [error, setError] = useState("");
  const requesting = useRef(false);
  const on = enabled && permission === "granted";
  const previous = useRef<{ on: boolean; waiting: Map<string, boolean> }>({ on: false, waiting: new Map() });
  useEffect(() => {
    const check = () => setPermission(permissionNow());
    window.addEventListener("focus", check);
    document.addEventListener("visibilitychange", check);
    return () => { window.removeEventListener("focus", check); document.removeEventListener("visibilitychange", check); };
  }, []);
  useEffect(() => {
    const current = new Map<string, boolean>();
    for (const session of sessions) {
      if (!loadedHosts.includes(splitSessionRef(session.key).host)) continue;
      current.set(session.key, session.waiting);
      // Only a witnessed transition may notify. Initial host snapshots and
      // enabling the setting establish a baseline, never replay a backlog.
      if (on && previous.current.on && session.waiting && previous.current.waiting.get(session.key) === false && permissionNow() === "granted") {
        try { new Notification(`${session.label} needs you`, { body: "The agent is waiting on an answer.", tag: session.key }); }
        catch { setError("Could not show a notification. Check browser and system notification settings."); }
      }
    }
    previous.current = { on, waiting: current };
  }, [sessions, loadedHosts, on]);
  function save(value: boolean) {
    setEnabled(value);
    try { localStorage.setItem(STORAGE_KEY, String(value)); }
    catch { setError("The notification setting could not be saved in this browser."); }
  }
  async function toggle() {
    if (requesting.current) return;
    setError("");
    const current = permissionNow();
    setPermission(current);
    if (enabled && current === "granted") { save(false); return; }
    if (current === "unavailable" || current === "denied") { save(false); return; }
    requesting.current = true;
    try {
      const result = current === "granted" ? current : await Notification.requestPermission();
      setPermission(result); save(result === "granted");
      if (result === "default") setError("Permission was not granted. Turn this on again to allow browser notifications.");
    } catch { setError("Could not request notification permission. Check your browser settings."); }
    finally { requesting.current = false; }
  }
  const help = error || (permission === "denied"
    ? "Notifications are blocked in your browser. Allow this site in browser settings to enable them."
    : permission === "unavailable" ? "Browser notifications are unavailable here. Mac app notifications are controlled separately."
      : "Browser notifications only. Mac app notifications are controlled separately. New requests notify after this is enabled.");
  return { on, toggle, help };
}
