import { useCallback, useSyncExternalStore } from "react";
import { api, type CheckpointAttemptReceipt, type CheckpointRecord, type CheckpointSelection, type CheckpointWorkProgress } from "./api";
import { checkpointStatus } from "./checkpointState";

type CheckpointOutcome = Pick<CheckpointRecord, "saved" | "summary_update" | "export_reason"> & { id?: string; label: string };

export type CheckpointAttempt =
  | { state: "running"; startedAt: number; progress?: CheckpointWorkProgress }
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
class CheckpointSelectionChanged extends Error {
  constructor() { super("Checkpoint selection changed. Open Checkpoint again to refresh the count; no checkpoint was saved."); }
}
export function checkpointRequestError(error: unknown): string {
  return error instanceof CheckpointSelectionChanged ? error.message : "Checkpoint result not confirmed. Check the latest checkpoint before trying again.";
}
export function requestCheckpoint(key: string, selection?: CheckpointSelection): Promise<CheckpointRecord> {
  const existing = pending.get(key);
  if (existing) return existing;
  const startedAt = Date.now();
  const requestId = crypto.randomUUID();
  // Defer dispatch until the promise is registered, even for synchronous callers.
  const request = Promise.resolve().then(async () => {
    let completeFromReceipt!: (result: CheckpointRecord) => void;
    const receiptResult = new Promise<CheckpointRecord>(resolve => { completeFromReceipt = resolve; });
    const stopProgress = watchProgress(key, requestId, startedAt, completeFromReceipt);
    try {
      const response = api.checkpoint(key, "manual", requestId, ...(selection ? [selection] as const : [])).then(result => {
        // Older servers may omit the ID on the direct response. A conflicting ID
        // is never safe to use, even when it arrived on this request's connection.
        if (result.request_id && result.request_id !== requestId) throw new Error("Checkpoint response identity mismatch");
        return result;
      });
      // The native POST can remain pending after the server has finished. Both
      // paths settle this one request; a late POST cannot publish or start recovery.
      return await Promise.race([response, receiptResult]);
    } catch (error) {
      stopProgress();
      publish(key, { state: "unknown", startedAt, recovering: true });
      const result = await recoverCheckpoint(key, requestId, startedAt, !!selection);
      if (!result) throw error;
      return result;
    } finally { stopProgress(); }
  }).then(result => {
    // Retain only status metadata, not another cached copy of conversation text.
    const outcome = { id: result.id, saved: result.saved, summary_update: result.summary_update, export_reason: result.export_reason, label: checkpointStatus(result).label };
    publish(key, { state: "complete", startedAt, result: outcome });
    return result;
  }, error => {
    if (error instanceof CheckpointSelectionChanged) {
      publish(key, { state: "complete", startedAt, result: { saved: false, summary_update: { state: "failed", reason: "source_changed" }, label: "Checkpoint not saved" } });
      throw error;
    }
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
async function recoverCheckpoint(key: string, requestId: string, startedAt: number, selected = false): Promise<CheckpointRecord | null> {
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
    if (receipt.state === "complete") return completedResult(receipt, requestId);
    if (selected && receipt.state === "failed" && receipt.error === "checkpoint_source_changed") throw new CheckpointSelectionChanged();
    // A failed/interrupted receipt without its exact result cannot establish
    // whether a checkpoint was saved. Preserve the existing unknown status.
    if (receipt.state !== "running") return null;
    // Cold starts may span many bounded model calls. A validated receipt means
    // work is still running, not an unknown result. Bound loss of contact, not
    // the duration of confirmed work; duplicate clicks still join this request.
    deadline = Date.now() + 180_000;
    publish(key, { state: "running", startedAt, progress: validProgress(receipt.progress) });
    await pause();
  }
  return null;
}

function completedResult(receipt: CheckpointAttemptReceipt, requestId: string): CheckpointRecord | null {
  if (receipt.request_id !== requestId || receipt.state !== "complete") return null;
  const result = receipt.result;
  return result?.request_id === requestId && typeof result.id === "string" && result.id ? result : null;
}

// Poll while the POST is outstanding. Exact terminal receipts are authoritative;
// failed reads and unconfirmed receipts never retry or cancel the checkpoint.
function watchProgress(key: string, requestId: string, startedAt: number, complete: (result: CheckpointRecord) => void): () => void {
  let stopped = false;
  let timer: ReturnType<typeof setTimeout>;
  let timeout: ReturnType<typeof setTimeout> | undefined;
  let controller: AbortController | undefined;
  let cancelRead: (() => void) | undefined;
  const poll = async () => {
    controller = new AbortController();
    try {
      const receipt = await Promise.race([
        api.checkpointAttempt(key, requestId, controller.signal),
        new Promise<null>(resolve => { cancelRead = () => resolve(null); timeout = setTimeout(cancelRead, 10_000); }),
      ]);
      if (!stopped && receipt?.request_id === requestId) {
        const result = completedResult(receipt, requestId);
        if (result) { stopped = true; complete(result); }
        else if (receipt.state === "running") publish(key, { state: "running", startedAt, progress: validProgress(receipt.progress) });
      }
    } catch { /* The POST still owns the result; recovery handles a lost response. */ }
    finally {
      clearTimeout(timeout); controller?.abort();
      if (!stopped) timer = setTimeout(poll, 2000);
    }
  };
  timer = setTimeout(poll, 1000);
  return () => { stopped = true; clearTimeout(timer); clearTimeout(timeout); controller?.abort(); cancelRead?.(); };
}

function validProgress(value: CheckpointWorkProgress | undefined): CheckpointWorkProgress | undefined {
  if (!value || !["checking", "writing", "reviewing", "repairing", "saving"].includes(value.stage)) return undefined;
  const { completed, total } = value;
  if (typeof completed === "number" && Number.isSafeInteger(completed) && completed >= 0
    && typeof total === "number" && Number.isSafeInteger(total) && total >= completed) return { stage: value.stage, completed, total };
  return { stage: value.stage };
}
