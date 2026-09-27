import { useCallback, useEffect, useState } from "react";
import { desktop } from "./desktop";
import { sessionRef, splitSessionRef } from "./hostTransport";
import { SessionView } from "./types";

const STORAGE = "rd.selectedSession";
function loadSelection(): string | null {
  try {
    const saved = JSON.parse(localStorage.getItem(STORAGE) ?? "null");
    if (typeof saved?.host !== "string" || typeof saved?.sessionKey !== "string" || !saved.host || !saved.sessionKey) return null;
    return sessionRef(saved.host, saved.sessionKey);
  } catch { return null; }
}
function saveSelection(key: string | null) {
  try {
    if (key === null) localStorage.removeItem(STORAGE);
    else {
      const ref = splitSessionRef(key);
      localStorage.setItem(STORAGE, JSON.stringify({ host: ref.host, sessionKey: ref.key }));
    }
  } catch { /* Selection still works when storage is unavailable. */ }
}

export function useSessionSelection(sessions: SessionView[], defaultKey: string | null, loadedHosts: string[]) {
  const [selection, setSelection] = useState(() => {
    const explicit = desktop()?.selectedSession;
    const key = explicit ?? loadSelection();
    return { key, pending: !!key, restoring: !explicit && !!key };
  });
  const [targetsRevision, setTargetsRevision] = useState(0);
  useEffect(() => {
    const changed = () => setTargetsRevision(n => n + 1);
    window.addEventListener("desktop-targets-changed", changed);
    return () => window.removeEventListener("desktop-targets-changed", changed);
  }, []);

  // Persist the user's choice synchronously, including a just-launched row
  // that has not reached its host feed yet. Any new choice cancels restoration.
  const selectSession = useCallback((key: string | null) => {
    saveSelection(key);
    setSelection({ key, pending: !!key, restoring: false });
  }, []);

  useEffect(() => {
    const { key, pending, restoring } = selection;
    const selected = sessions.find(s => s.key === key);
    if (selected) {
      if (pending) {
        saveSelection(key);
        setSelection({ key, pending: false, restoring: false });
        if (restoring && selected.group) window.dispatchEvent(new CustomEvent("reveal-sidebar-folder", { detail: selected.group }));
      }
      return;
    }
    if (key) {
      const { host } = splitSessionRef(key);
      const configured = host === "local" || desktop()?.targets.some(t => t.id === host);
      // A local snapshot cannot invalidate a remote choice. Wait for the
      // selected host's first successful snapshot, however long it is offline.
      if (configured && pending && (!restoring || !loadedHosts.includes(host))) return;
    }
    if (key !== defaultKey) selectSession(defaultKey);
  }, [selection, sessions, defaultKey, loadedHosts, targetsRevision, selectSession]);

  return { selectedKey: selection.key, selectSession };
}
