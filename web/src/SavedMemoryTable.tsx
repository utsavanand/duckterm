import { useEffect, useRef, useState } from "react";
import { memoryGet } from "./memoryViewApi";

type Page = { revisions: { id: string; created_at: number }[]; next_cursor: string | null };

export function SavedMemoryTable({ sessionKey, active, onMemory }: { sessionKey: string; active: boolean; onMemory?: (revision: string) => void }) {
  const [page, setPage] = useState<Page | null>(null);
  const [cursor, setCursor] = useState<string | null>(null);
  const [generation, refresh] = useState(0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const scope = useRef(sessionKey);
  useEffect(() => { scope.current = sessionKey; setPage(null); setCursor(null); }, [sessionKey]);
  useEffect(() => {
    if (!active) return;
    let live = true;
    setBusy(true); setError("");
    void memoryGet<Page>(sessionKey, "revisions", { limit: "20", ...(cursor ? { cursor } : {}) }).then(value => {
      if (!Array.isArray(value.revisions) || !value.revisions.every(row => typeof row.id === "string" && Number.isFinite(row.created_at) && Number.isFinite(new Date(row.created_at).getTime())) || !(value.next_cursor === null || typeof value.next_cursor === "string")) throw new Error("Invalid memory list");
      if (live && scope.current === sessionKey) setPage(previous => ({ ...value, revisions: cursor && previous ? [...previous.revisions, ...value.revisions].filter((row, index, rows) => rows.findIndex(r => r.id === row.id) === index) : value.revisions }));
    }).catch(() => { if (live) setError("Couldn’t load saved memories."); }).finally(() => { if (live) setBusy(false); });
    return () => { live = false; };
  }, [sessionKey, active, cursor, generation]);
  useEffect(() => {
    const reload = (event: Event) => { if ((event as CustomEvent<string>).detail === sessionKey) { setCursor(null); refresh(n => n + 1); } };
    window.addEventListener("duckterm-checkpoint", reload);
    return () => window.removeEventListener("duckterm-checkpoint", reload);
  }, [sessionKey]);
  return <>
    <div className="rd-saved-memory-table" tabIndex={0} aria-label="Saved memories" aria-busy={busy}>
      <table><thead><tr><th scope="col">Date</th><th scope="col">Memory</th></tr></thead>
        <tbody>{page?.revisions.map(row => <tr key={row.id}>
          <td><time dateTime={new Date(row.created_at).toISOString()}>{new Date(row.created_at).toLocaleString([], { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" })}</time></td>
          <td><button className="rd-timeline-link" disabled={!onMemory} onClick={() => onMemory?.(row.id)}>View memory ↗</button></td>
        </tr>)}{!page?.revisions.length && <tr><td colSpan={2}>{error || (busy || !page ? "Loading saved memories…" : "No saved memories yet.")}</td></tr>}</tbody>
      </table>
    </div>
    {error && <p role="alert">{page?.revisions.length ? error : ""} <button className="rd-timeline-link" onClick={() => { setCursor(null); refresh(n => n + 1); }}>Reload memories</button></p>}
    {page?.next_cursor && !error && <button className="rd-timeline-link" disabled={busy} onClick={() => setCursor(page.next_cursor)}>{busy ? "Loading…" : "Older memories"}</button>}
  </>;
}
