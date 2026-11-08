import { useEffect, useState } from "react";
import { authHeaders } from "./api";
import { routedFetch, splitSessionRef } from "./hostTransport";

const reasons = {
  agent_busy: "Waiting for this turn to finish",
  approval_pending: "Waiting for approval on this Mac",
  desktop_input: "Waiting for desktop input to finish",
  safe_prompt: "Waiting for a ready prompt",
  delivering: "Delivering to the agent",
};
interface PendingMessage {
  request_id: string;
  session_id: string;
  status: "queued" | "delivering";
  text: string;
  created_at: number;
  updated_at: number;
  expires_at: number;
  wait_reason: keyof typeof reasons;
}
interface PendingState { path: string; items: PendingMessage[]; checkedAt: number; stale: boolean; }

function readItems(body: unknown, key: string): PendingMessage[] {
  const items = (body as { mobile_messages?: unknown } | null)?.mobile_messages;
  if (!Array.isArray(items) || items.length > 5) throw new Error("Invalid pending messages");
  const seen = new Set<string>();
  for (const item of items) {
    if (!item || typeof item !== "object" || typeof item.request_id !== "string"
      || !/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.test(item.request_id)
      || seen.has(item.request_id) || item.session_id !== key
      || !["queued", "delivering"].includes(item.status)
      || typeof item.text !== "string" || !item.text.trim() || new TextEncoder().encode(item.text).length > 4096
      || typeof item.wait_reason !== "string" || !Object.hasOwn(reasons, item.wait_reason)
      || ![item.created_at, item.updated_at, item.expires_at].every(value => Number.isSafeInteger(value) && value >= 0 && value <= 8640000000000000)
      || item.updated_at < item.created_at || item.expires_at <= item.created_at) throw new Error("Invalid pending message");
    seen.add(item.request_id);
  }
  return items;
}

const clock = (timestamp: number) => new Date(timestamp).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });

/** Optional owner-only status, kept separate from the verified conversation. */
export function MobilePending({ sessionKey, active }: { sessionKey: string; active: boolean }) {
  const path = `/sessions/${encodeURIComponent(sessionKey)}/mobile-messages`;
  const [state, setState] = useState<PendingState>({ path, items: [], checkedAt: 0, stale: true });
  const [now, setNow] = useState(Date.now);
  const current = state.path === path ? state : undefined;
  useEffect(() => {
    let live = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let abort: (() => void) | undefined;
    let running = false;
    const visible = () => active && document.visibilityState !== "hidden";
    const markStale = () => setState(previous => previous.path === path ? { ...previous, stale: true } : { path, items: [], checkedAt: 0, stale: true });
    markStale();
    async function refresh() {
      if (!live || !visible() || running) return;
      running = true;
      const controller = new AbortController();
      let deadline: ReturnType<typeof setTimeout> | undefined;
      try {
        const unavailable = new Promise<never>((_, reject) => {
          abort = () => { controller.abort(); reject(new Error("Pending status unavailable")); };
          deadline = setTimeout(abort, 5000);
        });
        const items = await Promise.race([(async () => {
          const response = await routedFetch(path, { headers: authHeaders(), cache: "no-store", signal: controller.signal });
          if (!response.ok) throw new Error("Pending status unavailable");
          return readItems(await response.json(), splitSessionRef(sessionKey).key);
        })(), unavailable]);
        if (live && visible()) setState({ path, items, checkedAt: Date.now(), stale: false });
      } catch {
        if (live) markStale();
      } finally {
        clearTimeout(deadline);
        abort = undefined;
        running = false;
        if (live && visible()) timer = setTimeout(() => void refresh(), 3000);
      }
    }
    const visibility = () => {
      clearTimeout(timer);
      markStale();
      if (visible()) void refresh(); else abort?.();
    };
    document.addEventListener("visibilitychange", visibility);
    void refresh();
    return () => { live = false; clearTimeout(timer); abort?.(); document.removeEventListener("visibilitychange", visibility); };
  }, [path, sessionKey, active]);
  // Expiry removes the row even if the optional endpoint is unavailable.
  useEffect(() => {
    if (!active || !current?.items.length) return;
    const expiry = Math.min(...current.items.map(item => item.expires_at).filter(value => value > Date.now()));
    if (!Number.isFinite(expiry)) return;
    const timer = setTimeout(() => setNow(Date.now()), Math.max(0, expiry - Date.now()));
    return () => clearTimeout(timer);
  }, [active, current?.items, now]);
  const items = current?.items.filter(item => item.expires_at > Math.max(now, Date.now())) ?? [];
  if (!active || !items.length) return null;
  return <section className="rd-mobile-pending" aria-label="Pending iPhone messages">
    {current?.stale && <div className="rd-mobile-pending-stale" role="status">Status unavailable · Last confirmed at {clock(current.checkedAt)}</div>}
    {items.map(item => <article key={item.request_id}>
      <div className="rd-mobile-pending-meta"><span>From iPhone · <time dateTime={new Date(item.created_at).toISOString()}>{clock(item.created_at)}</time></span>
        <span className="rd-mobile-pending-reason">{current?.stale ? `Last confirmed: ${reasons[item.status === "delivering" ? "delivering" : item.wait_reason].toLowerCase()}` : reasons[item.status === "delivering" ? "delivering" : item.wait_reason]}</span>
      </div>
      <p>{item.text}</p>
    </article>)}
  </section>;
}
