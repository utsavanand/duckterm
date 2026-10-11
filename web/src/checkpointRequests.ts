import { useCallback, useSyncExternalStore } from "react";
import { api, type CheckpointRecord } from "./api";
import { checkpointStatus } from "./checkpointState";

type CheckpointOutcome = Pick<CheckpointRecord, "saved" | "summary_update" | "export_reason"> & { label: string };

export type CheckpointAttempt =
  | { state: "running"; startedAt: number }
  | { state: "complete"; startedAt: number; result: CheckpointOutcome }
  | { state: "unknown"; startedAt: number };

// Keys are the same host-qualified session references used by api.checkpoint.
// Requests outlive menus and selected panels; never retry a remote request locally.
const attempts = new Map<string, CheckpointAttempt>();
const pending = new Map<string, Promise<CheckpointRecord>>();
const listeners = new Map<string, Set<() => void>>();
function publish(key: string, attempt: CheckpointAttempt) {
  attempts.set(key, attempt);
  listeners.get(key)?.forEach(listener => listener());
}
export function useCheckpointAttempt(key: string) {
  const subscribe = useCallback((listener: () => void) => {
    const set = listeners.get(key) ?? new Set();
    listeners.set(key, set); set.add(listener);
    return () => { set.delete(listener); if (!set.size) listeners.delete(key); };
  }, [key]);
  return useSyncExternalStore(subscribe, () => attempts.get(key));
}
export function requestCheckpoint(key: string): Promise<CheckpointRecord> {
  const existing = pending.get(key);
  if (existing) return existing;
  const startedAt = Date.now();
  // Defer dispatch until the promise is registered, even for synchronous callers.
  const request = Promise.resolve().then(() => api.checkpoint(key, "manual")).then(result => {
    // Retain only status metadata, not another cached copy of conversation text.
    const outcome = { saved: result.saved, summary_update: result.summary_update, export_reason: result.export_reason, label: checkpointStatus(result).label };
    publish(key, { state: "complete", startedAt, result: outcome });
    return result;
  }, error => {
    // Transport failures cannot prove whether the server saved a checkpoint.
    publish(key, { state: "unknown", startedAt });
    throw error;
  }).finally(() => {
    pending.delete(key);
    window.dispatchEvent(new CustomEvent("duckterm-checkpoint", { detail: key }));
  });
  pending.set(key, request);
  publish(key, { state: "running", startedAt });
  window.dispatchEvent(new CustomEvent("duckterm-checkpoint-started", { detail: key }));
  return request;
}
