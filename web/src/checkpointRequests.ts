import { useCallback, useSyncExternalStore } from "react";
import { api, type CheckpointAttemptReceipt, type CheckpointRecord } from "./api";
import { checkpointStatus } from "./checkpointState";

type CheckpointOutcome = Pick<CheckpointRecord, "saved" | "summary_update" | "export_reason"> & { label: string };

export type CheckpointAttempt =
  | { state: "running"; startedAt: number }
  | { state: "complete"; startedAt: number; result: CheckpointOutcome }
  | { state: "unknown"; startedAt: number; recovering?: boolean };

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
  const requestId = crypto.randomUUID();
  // Defer dispatch until the promise is registered, even for synchronous callers.
  const request = Promise.resolve().then(async () => {
    try {
      const result = await api.checkpoint(key, "manual", requestId);
      // Older servers may omit the ID on the direct response. A conflicting ID
      // is never safe to use, even when it arrived on this request's connection.
      if (result.request_id && result.request_id !== requestId) throw new Error("Checkpoint response identity mismatch");
      return result;
    } catch (error) {
      publish(key, { state: "unknown", startedAt, recovering: true });
      const result = await recoverCheckpoint(key, requestId, startedAt);
      if (!result) throw error;
      return result;
    }
  }).then(result => {
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


// Recovery reads never create another checkpoint. Keep the original promise in
// `pending` throughout recovery so repeated clicks cannot issue a duplicate POST.
async function recoverCheckpoint(key: string, requestId: string, startedAt: number): Promise<CheckpointRecord | null> {
  let deadline = Date.now() + 180_000;
  let interval = 1000;
  const pause = async () => {
    await new Promise(resolve => setTimeout(resolve, Math.min(interval, Math.max(0, deadline - Date.now()))));
    interval = Math.min(interval * 2, 5000);
  };
  while (Date.now() < deadline) {
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;
    let receipt: CheckpointAttemptReceipt | null;
    try {
      // The native bridge cannot cancel an in-flight request. Race its result as
      // well as aborting browser fetches, so a stuck bridge cannot poll forever.
      receipt = await Promise.race([
        api.checkpointAttempt(key, requestId, controller.signal),
        new Promise<null>(resolve => { timer = setTimeout(() => resolve(null), Math.min(10_000, deadline - Date.now())); }),
      ]);
    } catch (error) {
      if (error instanceof Error && error.message.includes("Unsupported session operation")) return null;
      await pause();
      continue;
    } finally {
      clearTimeout(timer); controller.abort();
    }
    if (!receipt || receipt.request_id !== requestId) return null;
    if (receipt.state === "complete") {
      const result = receipt.result;
      return result?.request_id === requestId && typeof result.id === "string" && result.id ? result : null;
    }
    // A failed/interrupted receipt without its exact result cannot establish
    // whether a checkpoint was saved. Preserve the existing unknown status.
    if (receipt.state !== "running") return null;
    // Cold starts may span many bounded model calls. A validated receipt means
    // work is still running, not an unknown result. Bound loss of contact, not
    // the duration of confirmed work; duplicate clicks still join this request.
    deadline = Date.now() + 180_000;
    publish(key, { state: "running", startedAt });
    await pause();
  }
  return null;
}
