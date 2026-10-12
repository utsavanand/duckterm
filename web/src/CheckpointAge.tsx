import { useSyncExternalStore } from "react";
import { Duck } from "./Duck";
import { useNow } from "./useNow";
import "./memoryView.css";

export interface CheckpointClock { state: "known" | "never" | "unknown"; succeeded_at: number | null; checkpoint_id: string | null }
const clocks = new Map<string, CheckpointClock | null>();
const listeners = new Set<() => void>();
const subscribe = (listener: () => void) => { listeners.add(listener); return () => { listeners.delete(listener); }; };
// Share the existing selected-session checkpoint read; never fetch per render
// or run a provider on a display timer. Qualified keys keep remote clocks apart.
export function publishCheckpointClock(key: string, value?: CheckpointClock | null) {
  if (value?.state === "known" && (typeof value.succeeded_at !== "number" || !Number.isFinite(value.succeeded_at) || value.succeeded_at <= 0)) {
    value = { state: "unknown", succeeded_at: null, checkpoint_id: null };
  }
  if (value && !["known", "never", "unknown"].includes(value.state)) value = null;
  if (JSON.stringify(clocks.get(key)) === JSON.stringify(value ?? null)) return;
  clocks.set(key, value ?? null);
  while (clocks.size > 100) clocks.delete(clocks.keys().next().value!);
  listeners.forEach(listener => listener());
}
export function CheckpointAge({ sessionKey, showDuck = false }: { sessionKey: string; showDuck?: boolean }) {
  const clock = useSyncExternalStore(subscribe, () => clocks.get(sessionKey) ?? null);
  const now = useNow(60_000);
  const elapsed = clock?.state === "known" && typeof clock.succeeded_at === "number" ? Math.max(0, now - clock.succeeded_at) : null;
  const mins = elapsed === null ? 0 : Math.floor(elapsed / 60_000);
  const age = mins < 1 ? "just now" : mins < 60 ? `${mins}m ago` : mins < 1440 ? `${Math.floor(mins / 60)}h ago` : `${Math.floor(mins / 1440)}d ago`;
  const label = !clock ? "Checkpoint age unavailable" : clock.state === "never" ? "Never successfully checkpointed" : clock.state === "unknown" ? "Checkpoint age unknown" : `Last checkpointed ${age}`;
  const old = clock?.state === "never" || (elapsed !== null && elapsed >= 86400000);
  return <p className={`rd-checkpoint-age${old ? " stale" : ""}`} title={clock?.succeeded_at ? `Last successful checkpoint: ${new Date(clock.succeeded_at).toLocaleString()}. Later failed attempts do not reset this clock.` : label}>
    {showDuck && <Duck pose={clock?.state === "never" || (elapsed !== null && elapsed >= 3 * 86400000) ? "waving" : "idle"} size={28} />}<span aria-hidden="true">◷</span> {label}
  </p>;
}
