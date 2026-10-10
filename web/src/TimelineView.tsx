import { Fragment, useEffect, useRef, useState } from "react";
import { api, CheckpointRecord, forkMergeHistory, forkMergeService, ForkMergeHistory, TimelineEntry, TimelinePage } from "./api";
import { splitSessionRef } from "./hostTransport";
import { SessionView } from "./types";
import { HistoryView } from "./HistoryView";
import { CheckpointDetails } from "./CheckpointStatus";
import { checkpointNotice } from "./checkpointState";
import "./timeline.css";

const PROGRESS_KINDS = "delivered,learned,next_action,completed,decision,restart,model,harness,needs-you";
export const TIMELINE_MILESTONES = `checkpoint,artifact,${PROGRESS_KINDS}`;
const FILTERS = [
  ["all", "All", TIMELINE_MILESTONES], ["checkpoint", "Checkpoints", "checkpoint"],
  ["work", "Progress", PROGRESS_KINDS],
  ["artifact", "Artifacts", "artifact"],
] as const;
const LABELS: Record<string, string> = { prompt: "Owner message", delivered: "Delivered", learned: "Task learning", next_action: "Next action", completed: "Completed", decision: "Owner decision", restart: "Restart", model: "Model changed", harness: "Harness switched", "needs-you": "Needs you", artifact: "Artifact" };

export function TimelineView({ session, active = true, checkpointTarget, onArtifacts, onMessages }: {
  session: SessionView; active?: boolean; checkpointTarget?: number; onArtifacts?: () => void; onMessages?: () => void;
}) {
  const [filter, setFilter] = useState("all");
  const [data, setData] = useState<TimelinePage | null>(null);
  const [checkpoints, setCheckpoints] = useState<CheckpointRecord[]>([]);
  const [merges, setMerges] = useState<ForkMergeHistory[]>([]);
  const [error, setError] = useState("");
  const [detailError, setDetailError] = useState("");
  const [busy, setBusy] = useState(false);
  const [retryBusy, setRetryBusy] = useState(false);
  const [retryNotice, setRetryNotice] = useState("");
  const retryPending = useRef(new Set<string>());
  const retryGeneration = useRef(0);
  const [unsupported, setUnsupported] = useState(false);
  const pages = useRef(1);
  const loadMore = useRef<() => void>(() => undefined);
  const latest = useRef<HTMLDivElement>(null);
  const jumped = useRef(0);
  const keyRef = useRef(session.key);
  keyRef.current = session.key;
  useEffect(() => {
    setRetryNotice(""); setRetryBusy(retryPending.current.has(session.key));
    const generation = retryGeneration.current;
    return () => { retryGeneration.current = generation + 1; };
  }, [session.key]);
  async function retrySummary() {
    const key = session.key, generation = retryGeneration.current;
    if (retryPending.current.has(key)) return;
    retryPending.current.add(key); setRetryBusy(true); setRetryNotice("");
    try {
      const cp = await api.checkpoint(key, "manual");
      if (keyRef.current === key && retryGeneration.current === generation) setRetryNotice(checkpointNotice(cp));
      window.dispatchEvent(new CustomEvent("duckterm-checkpoint", { detail: key }));
    } catch (cause) {
      if (keyRef.current === key && retryGeneration.current === generation) setRetryNotice(`Summary update failed: ${(cause as Error).message}`);
    } finally {
      retryPending.current.delete(key);
      if (keyRef.current === key) setRetryBusy(false);
    }
  }
  useEffect(() => { setFilter("all"); }, [session.key]);
  useEffect(() => { if (checkpointTarget) setFilter("checkpoint"); }, [checkpointTarget]);
  useEffect(() => {
    if (checkpointTarget && checkpointTarget !== jumped.current && data?.entries[0]?.kind === "checkpoint" && filter === "checkpoint") {
      latest.current?.scrollIntoView?.({ block: "center" }); jumped.current = checkpointTarget;
    }
  }, [checkpointTarget, data, filter]);
  useEffect(() => {
    setData(null); setCheckpoints([]); setMerges([]); setError(""); setDetailError(""); setUnsupported(false);
    pages.current = 1;
    if (!active) return;
    let live = true, pending = false, unavailable = false, refreshRequested = false;
    const kinds = FILTERS.find(f => f[0] === filter)?.[2] ?? "";
    async function load(more = false) {
      if (!live || unavailable || pending || document.visibilityState === "hidden") return;
      pending = true; setBusy(true);
      const wanted = pages.current + (more ? 1 : 0);
      try {
        // Rebuild the visible pages from one fresh snapshot. Never mix cursors
        // across filters, sessions, or a concurrent refresh.
        let result = await api.timeline(session.key, kinds);
        let count = 1;
        while (count < wanted && result.next_cursor) {
          const next = await api.timeline(session.key, kinds, result.next_cursor);
          if (!live) return;
          result = { ...result, entries: [...result.entries, ...next.entries], next_cursor: next.next_cursor };
          count++;
        }
        if (!live) return;
        pages.current = count; setData(result); setError("");
        const details = await Promise.allSettled([api.checkpoints(session.key), forkMergeHistory(session.key)]);
        if (!live) return;
        if (details[0].status === "fulfilled") setCheckpoints(details[0].value.checkpoints);
        if (details[1].status === "fulfilled") setMerges(details[1].value.merges);
        setDetailError(details.some(d => d.status === "rejected") ? "Some saved details could not be refreshed. Previously loaded details are retained." : "");
      } catch (cause) {
        if (!live) return;
        // Older remote servers retain the previous digest and checkpoint view.
        const message = (cause as Error).message;
        const olderNative = splitSessionRef(session.key).host !== "local" && message === "Unsupported session operation";
        if (olderNative || /404|not found|unknown endpoint/i.test(message)) { unavailable = true; setUnsupported(true); }
        else setError(message || "Timeline could not be loaded.");
      } finally {
        pending = false;
        if (live) setBusy(false);
        if (live && refreshRequested) { refreshRequested = false; void load(); }
      }
    }
    loadMore.current = () => { void load(true); };
    const visible = () => { void load(); };
    void load();
    const timer = setInterval(visible, 10_000);
    document.addEventListener("visibilitychange", visible);
    const saved = (event: Event) => { if ((event as CustomEvent<string>).detail === session.key) { if (pending) refreshRequested = true; else visible(); } };
    window.addEventListener("duckterm-checkpoint", saved);
    return () => { live = false; clearInterval(timer); document.removeEventListener("visibilitychange", visible); window.removeEventListener("duckterm-checkpoint", saved); loadMore.current = () => undefined; };
  }, [session.key, active, filter]);
  if (unsupported) return <><p className="rd-panel-empty">This server provides the earlier history view.</p><HistoryView session={session} active={active} /></>;
  let previousDay = "";
  return <section className="rd-timeline" aria-label="Session timeline">
    <h1>Timeline <span>{data ? `${data.summary.total} ${data.summary.total === 1 ? "entry" : "entries"}` : ""}</span></h1>
    <p className="rd-timeline-intro">History is recorded automatically. Checkpoint requests a summary update now. The full conversation stays in {onMessages ? <button className="rd-timeline-link" onClick={onMessages}>Messages</button> : "Messages"}.</p>
    <div className="rd-timeline-filters" aria-label="Timeline filter">{FILTERS.map(([id, label]) => <button key={id} className="rd-btn rd-btn-sm rd-btn-ghost" aria-pressed={filter === id} onClick={() => setFilter(id)}>{label}</button>)}</div>
    {error && <p role="alert">Timeline unavailable: {error}</p>}
    {detailError && <p role="status">{detailError}</p>}
    {retryNotice && <p role="status">{retryNotice}</p>}
    {!data && busy && <p role="status">Loading timeline…</p>}
    {data?.entries.length === 0 && <p className="rd-panel-empty">No {filter === "all" ? "timeline entries" : FILTERS.find(f => f[0] === filter)?.[1].toLowerCase()} yet.</p>}
    {data?.entries.map((entry, i) => {
      const day = new Date(entry.ts).toLocaleDateString(undefined, { year: "numeric", month: "long", day: "numeric" });
      const showDay = day !== previousDay; previousDay = day;
      const cpId = entry.refs.find(r => r.source === "checkpoints")?.id;
      const cp = checkpoints.find(c => c.id === cpId);
      return <Fragment key={entry.id}>
        {showDay && <h2 className="rd-timeline-day">{day}</h2>}
        <article className="rd-timeline-entry">
          <time dateTime={new Date(entry.ts).toISOString()}>{new Date(entry.ts).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}</time>
          <span aria-hidden="true" className="rd-timeline-mark">{entry.kind === "checkpoint" ? "⚑" : entry.kind === "artifact" ? "▤" : "·"}</span>
          <div ref={i === 0 ? latest : undefined}>{entry.kind === "checkpoint"
            ? <CheckpointDetails checkpoint={cp ?? { id: cpId ?? entry.id, label: entry.detail.text ?? "Checkpoint", created_at: entry.ts, summary: entry.detail.summary ?? "", summary_state: entry.detail.summary_state, record: { prompts: [], files: [], tools: [], event_count: 0 } }} open={!!checkpointTarget && i === 0} onRetry={cp && !["archived", "merged", "terminated"].includes(session.state) ? () => void retrySummary() : undefined} retrying={retryBusy} />
            : <EventContent entry={entry} onArtifacts={onArtifacts} />}</div>
        </article>
      </Fragment>;
    })}
    {data?.next_cursor && <button className="rd-btn rd-btn-sm rd-btn-ghost" disabled={busy} onClick={() => loadMore.current()}>{busy ? "Loading…" : "Load older entries"}</button>}
    {(filter === "all" || filter === "checkpoint") && merges.length > 0 && <section className="rd-timeline-merges"><h2>Fork merges</h2>{merges.map(row => <details key={row.id}>
      <summary>{new Date(row.createdAt).toLocaleString()} · {row.delivery}{row.statusAvailable === false ? " (last recorded)" : ""} · {row.parentDeleted ? "Parent deleted" : row.keepOpen ? "Child kept open" : row.childClosed ? "Child closed" : "Child closure pending"}</summary>
      <p>Parent checkpoint: {row.checkpoint}</p><p className="rd-timeline-full-text">{row.summary}</p>
      {!row.keepOpen && !row.childClosed && splitSessionRef(session.key).key === row.child && <button className="rd-btn rd-btn-sm rd-btn-ghost" onClick={async () => {
        const key = session.key;
        try {
          await forkMergeService(key).send({ summary: row.summary, keepOpen: false, requestKey: row.id.split(":").at(-1)! });
          const next = await forkMergeHistory(key);
          if (keyRef.current === key) { setMerges(next.merges); setDetailError(""); }
        } catch (cause) { if (keyRef.current === key) setDetailError((cause as Error).message); }
      }}>Finish closing child</button>}
    </details>)}</section>}
  </section>;
}
function EventContent({ entry, onArtifacts }: { entry: TimelineEntry; onArtifacts?: () => void }) {
  const d = entry.detail;
  const label = d.bucket === "user_learnings" ? "Working together" : LABELS[entry.kind] ?? entry.kind;
  return <>
    <strong>{label}{d.removed ? " · removed" : ""}</strong>
    {entry.kind === "artifact" && !d.removed && onArtifacts
      ? <p><button className="rd-timeline-link" onClick={onArtifacts}>{d.text || entry.one_line}</button></p>
      : <p className="rd-timeline-full-text">{d.text || entry.one_line}</p>}
    {d.answer && <p className="rd-timeline-full-text">{d.answer}</p>}
    {(d.from_harness || d.to_harness || d.model) && <p>{[d.from_harness, d.to_harness].filter(Boolean).join(" → ")}{d.model && ` · ${d.model}`}</p>}
  </>;
}
