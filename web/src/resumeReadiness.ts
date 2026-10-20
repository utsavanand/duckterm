import { useSyncExternalStore } from "react";

// Recovery readiness belongs to a session, not just the mounted card. Keep an
// unresolved Undo blocked even when its card unmounts or another control resumes.
const blocked = new Set<string>();
const undoPending = new Set<string>();
const listeners = new Set<() => void>();
function subscribe(listener: () => void) {
  listeners.add(listener);
  return () => { listeners.delete(listener); };
}
export function setRecoveryResumeAllowed(key: string, allowed: boolean) {
  const before = blocked.has(key);
  if (allowed && undoPending.has(key)) return;
  if (allowed) blocked.delete(key); else blocked.add(key);
  if (before !== blocked.has(key)) for (const listener of listeners) listener();
}
export function recoveryBlocksResume(key: string) { return blocked.has(key); }
export function useRecoveryResumeBlocked(key: string) {
  return useSyncExternalStore(subscribe, () => recoveryBlocksResume(key));
}

// A readiness read can still see the old binding while the write is in flight.
// Only the request owner can finish this phase; card unmount must not release it.
export function beginRecoveryUndo(key: string): boolean {
  if (undoPending.has(key)) return false;
  undoPending.add(key); blocked.add(key);
  for (const listener of listeners) listener();
  return true;
}
export function finishRecoveryUndo(key: string) {
  if (undoPending.delete(key)) for (const listener of listeners) listener();
}
export function useRecoveryUndoPending(key: string) {
  return useSyncExternalStore(subscribe, () => undoPending.has(key));
}
