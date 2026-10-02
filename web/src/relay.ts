import { useCallback, useEffect, useState } from "react";
import { api, RelayState } from "./api";

const EMPTY: RelayState = { notes: [], rules: [], open: 0 };
// A poll that never answers (the Mac app's web view after sleep or a network
// change) used to stop polling for good, leaving Needs you and the Oracle
// chat frozen with no error. Each poll is now abandoned after this long.
export const POLL_TIMEOUT_MS = 10_000;

// Polls load() every intervalMs until stopped, abandoning any attempt that
// takes longer than POLL_TIMEOUT_MS, and polls at once when the window comes
// back into view.
function poll(load: (signal: AbortSignal) => Promise<void>, intervalMs: number): () => void {
  let stopped = false;
  let timer: ReturnType<typeof setTimeout> | undefined;
  let inflight: AbortController | undefined;
  async function run() {
    clearTimeout(timer);
    inflight?.abort();
    const attempt = new AbortController();
    inflight = attempt;
    let deadline: ReturnType<typeof setTimeout> | undefined;
    try {
      // The race ends the attempt even if the request ignores its abort.
      await Promise.race([
        load(attempt.signal),
        new Promise((_, reject) => {
          deadline = setTimeout(() => { attempt.abort(); reject(new Error("timed out")); }, POLL_TIMEOUT_MS);
        }),
      ]);
    } catch {
      /* keep the last state while the server is briefly unreachable */
    } finally {
      clearTimeout(deadline);
      if (!stopped && inflight === attempt) timer = setTimeout(run, intervalMs);
    }
  }
  const onVisible = () => { if (document.visibilityState === "visible") void run(); };
  document.addEventListener("visibilitychange", onVisible);
  window.addEventListener("focus", onVisible);
  void run();
  return () => {
    stopped = true;
    clearTimeout(timer);
    inflight?.abort();
    document.removeEventListener("visibilitychange", onVisible);
    window.removeEventListener("focus", onVisible);
  };
}

// Oracle Relay notes and rules, refreshed every few seconds while mounted and
// enabled (voice mode reads them only while it is on).
export function useRelay(enabled = true): RelayState & { refresh: () => void; loaded: boolean } {
  const [state, setState] = useState<RelayState>(EMPTY);
  const [loaded, setLoaded] = useState(false);
  const [tick, setTick] = useState(0);
  const refresh = useCallback(() => setTick((t) => t + 1), []);
  useEffect(() => {
    if (!enabled) {
      setLoaded(false);
      return;
    }
    let stopped = false;
    const stop = poll(async (signal) => {
      const next = await api.relay(signal);
      if (!stopped && !signal.aborted) {
        setState(next);
        setLoaded(true);
      }
    }, 4000);
    return () => { stopped = true; stop(); };
  }, [tick, enabled]);
  return { ...state, refresh, loaded };
}

// Just the open count, for the topbar's Oracle button.
export function useRelayCount(): number {
  const [open, setOpen] = useState(0);
  useEffect(() => {
    let stopped = false;
    const stop = poll(async (signal) => {
      const r = await api.relayCount(signal);
      if (!stopped && !signal.aborted) setOpen(r.open);
    }, 5000);
    return () => { stopped = true; stop(); };
  }, []);
  return open;
}
