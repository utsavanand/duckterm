import { useCallback, useEffect, useState } from "react";
import { api, RelayState } from "./api";

const EMPTY: RelayState = { notes: [], rules: [], open: 0 };

// Oracle Relay notes and rules, refreshed every few seconds while mounted.
export function useRelay(): RelayState & { refresh: () => void } {
  const [state, setState] = useState<RelayState>(EMPTY);
  const [tick, setTick] = useState(0);
  const refresh = useCallback(() => setTick((t) => t + 1), []);
  useEffect(() => {
    let stopped = false;
    let timer: ReturnType<typeof setTimeout>;
    async function load() {
      try {
        const next = await api.relay();
        if (!stopped) setState(next);
      } catch {
        /* keep the last state while the server is briefly unreachable */
      } finally {
        if (!stopped) timer = setTimeout(load, 4000);
      }
    }
    void load();
    return () => { stopped = true; clearTimeout(timer); };
  }, [tick]);
  return { ...state, refresh };
}

// Just the open count, for the topbar's Oracle button.
export function useRelayCount(): number {
  const [open, setOpen] = useState(0);
  useEffect(() => {
    let stopped = false;
    let timer: ReturnType<typeof setTimeout>;
    async function load() {
      try {
        const r = await api.relayCount();
        if (!stopped) setOpen(r.open);
      } catch { /* keep the last count */ }
      finally { if (!stopped) timer = setTimeout(load, 5000); }
    }
    void load();
    return () => { stopped = true; clearTimeout(timer); };
  }, []);
  return open;
}
