import { useHostData } from "./useHostData";
import { sessionRef } from "./hostTransport";

export function useInboxCounts(enabled = true): Record<string, number> {
  const { data } = useHostData<{ counts: Record<string, number> }>("/session-inbox-counts", 3000, enabled);
  return Object.fromEntries(Object.entries(data).flatMap(([host, value]) =>
    Object.entries(value.counts).map(([key, count]) => [sessionRef(host, key), count])));
}
