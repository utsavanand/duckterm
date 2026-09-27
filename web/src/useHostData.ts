import { useCallback, useEffect, useState } from "react";
import { desktop } from "./desktop";
import { hostFetch } from "./hostTransport";
import { authHeaders } from "./api";

// Poll each host independently: a slow/offline remote never blocks local data.
export function useHostData<T>(path: string, interval = 3000) {
  const [data, setData] = useState<Record<string, T>>({});
  const [revision, setRevision] = useState(0);
  const refresh = useCallback(() => setRevision(n => n + 1), []);
  useEffect(() => {
    let stopped = false;
    const hosts = desktop()?.targets.map(h => h.id) ?? ["local"];
    setData(previous => Object.fromEntries(Object.entries(previous).filter(([host]) => hosts.includes(host))));
    const timers = new Map<string, ReturnType<typeof setTimeout>>();
    const poll = async (host: string) => {
      try {
        const response = await hostFetch(host, path, { headers: authHeaders() });
        if (!response.ok) return;
        const next = await response.json() as T;
        if (!stopped) setData(previous => ({ ...previous, [host]: next }));
      } catch { /* retain the last known data while this host reconnects */ }
      finally { if (!stopped) timers.set(host, setTimeout(() => void poll(host), interval)); }
    };
    hosts.forEach(host => void poll(host));
    window.addEventListener("desktop-targets-changed", refresh);
    return () => { stopped = true; timers.forEach(clearTimeout); window.removeEventListener("desktop-targets-changed", refresh); };
  }, [path, interval, revision, refresh]);
  return { data, refresh };
}
