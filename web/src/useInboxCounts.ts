import { useMemo } from "react";
import { useHostData } from "./useHostData";
import { sessionRef } from "./hostTransport";

type InboxCounts = { counts: Record<string, number>; unanswered_counts?: Record<string, number> };
export function useInboxCounts(enabled = true) {
  const { data } = useHostData<InboxCounts>("/session-inbox-counts", 3000, enabled);
  return useMemo(() => {
    const collect = (field: keyof InboxCounts): Record<string, number> => Object.fromEntries(
      Object.entries(data).flatMap(([host, value]) => Object.entries(value[field] ?? {})
        .map(([key, count]) => [sessionRef(host, key), count])));
    // Older hosts do not report reply-required counts: do not mislabel unread notices.
    return { pending: collect("counts"), unanswered: collect("unanswered_counts") };
  }, [data]);
}
