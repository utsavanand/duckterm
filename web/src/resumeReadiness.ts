import { useSyncExternalStore } from "react";

// Recovery readiness belongs to a session, not just the mounted card. Keep an
// unresolved Undo blocked even when its card unmounts or another control resumes.
const blocked = new Set<string>();
const listeners = new Set<() => void>();
function subscribe(listener: () => void) {
  listeners.add(listener);
  return () => { listeners.delete(listener); };
}
export function setRecoveryResumeAllowed(key: string, allowed: boolean) {
  const before = blocked.has(key);
  if (allowed) blocked.delete(key); else blocked.add(key);
  if (before !== blocked.has(key)) for (const listener of listeners) listener();
}
export function recoveryBlocksResume(key: string) { return blocked.has(key); }
export function useRecoveryResumeBlocked(key: string) {
  return useSyncExternalStore(subscribe, () => recoveryBlocksResume(key));
}
