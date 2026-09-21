import { useEffect, useState } from "react";
import { authHeaders } from "./api";

export function useInboxCounts(): Record<string, number> {
  const [counts, setCounts] = useState<Record<string, number>>({});
  useEffect(() => {
    let stopped = false;
    let timer: ReturnType<typeof setTimeout>;
    async function refresh() {
      try {
        const response = await fetch("/session-inbox-counts", { headers: authHeaders(), cache: "no-store" });
        if (!response.ok) return;
        const data = await response.json() as { counts: Record<string, number> };
        if (!stopped) setCounts(data.counts);
      } catch { /* Keep the last count while the connection recovers. */ }
      finally { if (!stopped) timer = setTimeout(refresh, 3000); }
    }
    void refresh();
    return () => { stopped = true; clearTimeout(timer); };
  }, []);
  return counts;
}
