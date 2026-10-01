import { useEffect, useState } from "react";
import { api } from "./api";

// Opening a session that raised its hand counts as attending to it, once it
// has been on a visible screen for a moment (owner decision, 2026-09-30: hands
// stay up until the owner attends, not until the agent's next event).
export const ATTEND_AFTER_MS = 1000;

export function useAttended(key: string | null, raised: boolean): void {
  const [visible, setVisible] = useState(() => document.visibilityState === "visible");
  useEffect(() => {
    const update = () => setVisible(document.visibilityState === "visible");
    document.addEventListener("visibilitychange", update);
    return () => document.removeEventListener("visibilitychange", update);
  }, []);
  useEffect(() => {
    if (!key || !raised || !visible) return;
    const timer = setTimeout(() => void api.sessionAttended(key).catch(() => {}), ATTEND_AFTER_MS);
    return () => clearTimeout(timer);
  }, [key, raised, visible]);
}
