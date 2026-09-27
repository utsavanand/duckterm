import { useEffect, useState } from "react";

// Keep recent views warm without parsing every hidden session's output.
// Eviction closes only a browser subscription; the server-owned PTY continues.
export const TERMINAL_CACHE_LIMIT = 3;

export function retainTerminals(
  previous: string[], available: string[], selected: string | null,
): string[] {
  const live = new Set(available);
  const next = [...new Set([
    ...(selected && live.has(selected) ? [selected] : []), ...previous,
  ])].filter(key => live.has(key)).slice(0, TERMINAL_CACHE_LIMIT);
  return next.length === previous.length && next.every((key, i) => key === previous[i])
    ? previous : next;
}

export function useTerminalCache(available: string[], selected: string | null): string[] {
  const [recent, setRecent] = useState<string[]>([]);
  const next = retainTerminals(recent, available, selected);
  useEffect(() => { if (next !== recent) setRecent(next); }, [next, recent]);
  return next;
}
