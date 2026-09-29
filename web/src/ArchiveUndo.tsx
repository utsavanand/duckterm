import { useCallback, useEffect, useRef, useState } from "react";
import { authHeaders } from "./api";
import { desktop } from "./desktop";
import { hostFetch, routedFetch, sessionRef } from "./hostTransport";
import "./archiveUndo.css";

type Request = { id: string; session_key: string; name: string; deadline: number; status: string; error?: string; offline?: boolean };
const changed = "archive-requests-changed";

export async function requestArchive(key: string): Promise<void> {
  const response = await routedFetch(`/sessions/${encodeURIComponent(key)}/archive-undo`, {
    method: "POST", headers: authHeaders(),
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || "Archive failed");
  // Qualify local responses too, using the selected session's canonical key.
  window.dispatchEvent(new CustomEvent(changed, { detail: { ...data, session_key: key } }));
}

export function useArchiveRequests() {
  const [requests, setRequests] = useState<Request[]>([]);
  const revision = useRef(0);
  const invalidate = useCallback(() => { ++revision.current; }, []);
  const refresh = useCallback(async () => {
    const stamp = ++revision.current;
    const hosts = desktop()?.targets ?? [{ id: "local", name: "This Mac" }];
    const results = await Promise.all(hosts.map(async host => {
      try {
        const response = await hostFetch(host.id, "/archive-requests", { cache: "no-store" });
        if (!response.ok) throw new Error("Host unavailable");
        const data = await response.json();
        return { host: host.id, requests: (data.requests ?? []) as Request[] };
      } catch { return { host: host.id, requests: null }; }
    }));
    if (stamp !== revision.current) return;
    setRequests(previous => results.flatMap(result => result.requests ?? previous.filter(r => {
      const prefix = sessionRef(result.host, "");
      return result.host === "local" ? !r.session_key.startsWith("~remote~") : r.session_key.startsWith(prefix);
    }).map(r => ({ ...r, offline: true }))));
  }, []);
  useEffect(() => {
    const update = (event: Event) => {
      const value = (event as CustomEvent<Request>).detail;
      ++revision.current;
      if (value) setRequests(rows => [...rows.filter(r => r.session_key !== value.session_key), value]);
      void refresh();
    };
    let stopped = false;
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      await refresh();
      if (!stopped) timer = setTimeout(() => { void poll(); }, 1000);
    };
    void poll();
    window.addEventListener(changed, update);
    window.addEventListener("focus", refresh);
    window.addEventListener("desktop-targets-changed", refresh);
    return () => { stopped = true; invalidate(); clearTimeout(timer); window.removeEventListener(changed, update); window.removeEventListener("focus", refresh); window.removeEventListener("desktop-targets-changed", refresh); };
  }, [refresh, invalidate]);
  return { requests, refresh };
}

function ArchiveNotice({ request, refresh }: { request: Request; refresh: () => Promise<void> }) {
  const [dismissed, setDismissed] = useState(false);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const undo = useRef<HTMLButtonElement>(null);
  const remaining = Math.max(0, Math.ceil((request.deadline - Date.now()) / 1000));
  const canUndo = request.status === "pending" && remaining > 0 && !request.offline;
  useEffect(() => { undo.current?.focus(); }, []);
  async function cancel() {
    setBusy(true);
    try {
      const response = await routedFetch(`/sessions/${encodeURIComponent(request.session_key)}/archive-undo`, {
        method: "DELETE", headers: authHeaders({ "Content-Type": "application/json" }), body: JSON.stringify({ id: request.id }),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || "Undo failed");
      // Refresh the authoritative session list before restoring its selection.
      await refresh();
      window.dispatchEvent(new CustomEvent("select-host-session", { detail: request.session_key }));
    } catch (e) { setError((e as Error).message); setBusy(false); }
  }
  if (dismissed) return null;
  return <div className="rd-archive-notice" role="region" aria-label={`Archive notification for ${request.name}`} onKeyDown={event => {
    if (event.key === "Escape") { event.preventDefault(); setDismissed(true); }
  }}>
    <span role="status">{canUndo ? "Archived" : request.offline ? "Archive pending — host unavailable" : request.error ? "Archive pending — retrying" : "Completing archive"}<small>{request.name}</small></span>
    {canUndo && <><button ref={undo} disabled={busy} onClick={() => void cancel()}>Undo</button><span aria-hidden="true">{remaining}s</span></>}
    <button aria-label="Dismiss archive notification" onClick={() => setDismissed(true)}>×</button>
    {error && <span role="alert">{error}</span>}
  </div>;
}

export function ArchiveUndo({ requests, refresh }: ReturnType<typeof useArchiveRequests>) {
  return <div className="rd-archive-notices">{requests.map(request => <ArchiveNotice key={request.id} request={request} refresh={refresh} />)}</div>;
}
