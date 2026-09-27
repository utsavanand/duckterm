import { useHostData } from "./useHostData";
import { sessionRef } from "./hostTransport";

export function useInboxCounts(): Record<string, number> {
  const { data } = useHostData<{ counts: Record<string, number> }>("/session-inbox-counts");
  return Object.fromEntries(Object.entries(data).flatMap(([host, value]) =>
    Object.entries(value.counts).map(([key, count]) => [sessionRef(host, key), count])));
}
