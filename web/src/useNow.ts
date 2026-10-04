import { useEffect, useState } from "react";

// Keep clocks below Dashboard: only their subscribers redraw. Display clocks
// stop while hidden; voice may keep its completion clock in the background.
export function useNow(intervalMs = 1000, active = true, visibleOnly = true): number {
  const [now, setNow] = useState(Date.now);
  useEffect(() => {
    let timer: ReturnType<typeof setInterval> | undefined;
    const sync = () => {
      clearInterval(timer);
      if (!active || (visibleOnly && document.visibilityState === "hidden")) return;
      setNow(Date.now());
      timer = setInterval(() => setNow(Date.now()), intervalMs);
    };
    sync();
    if (visibleOnly) document.addEventListener("visibilitychange", sync);
    return () => {
      clearInterval(timer);
      if (visibleOnly) document.removeEventListener("visibilitychange", sync);
    };
  }, [intervalMs, active, visibleOnly]);
  return now;
}
