import { routedFetch as fetch, splitSessionRef } from "./hostTransport";
import { useEffect, useState } from "react";
import { api, forkMergeService, CheckpointRecord, forkMergeHistory, ForkMergeHistory } from "./api";
import { useNow } from "./useNow";
import { SessionView } from "./types";

// Middle-pane History tab: the session's accumulated digest archive — every
// validated deliverable / learning / next action as it was added, plus the
// checkpoint timeline. Items come from /sessions/:key/digest (durable rows
// that grow over the session's life); until that endpoint exists on the
// running server, the latest digest blob on the session row is the fallback.

interface DigestItem {
  id: string;
  bucket: string;
  text: string;
  status: "active" | "done";
  created_at: number;
}

const BUCKETS: [string, string, string][] = [
  // [bucket key, title, mark]
  ["deliverables", "Delivered", "✓"],
  ["learnings", "Task learnings", "◆"],
  ["user_learnings", "Working together", "◇"],
  ["next_actions", "Next actions", "→"],
];

export function HistoryView({ session, active = true }: { session: SessionView; active?: boolean }) {
  useNow(60_000, active);
  const p = session.progress;
  const started = new Date(session.startedAt);
  const updated = session.progressAt ? agoLabel(session.progressAt) : null;
  const [merges, setMerges] = useState<ForkMergeHistory[]>([]);
  const [mergeError, setMergeError] = useState("");
  const [items, setItems] = useState<DigestItem[] | null>(null);
  const [checkpoints, setCheckpoints] = useState<CheckpointRecord[]>([]);

  useEffect(() => {
    setItems(null); setCheckpoints([]); setMerges([]); setMergeError("");
  }, [session.key]);

  useEffect(() => {
    if (!active) return;
    let live = true, digestPending = false, mergesPending = false, checkpointsLoaded = false, checkpointsPending = false;
    const load = () => {
      if (!live || document.visibilityState === "hidden") return;
      if (!digestPending) {
        digestPending = true;
        void fetch(`/sessions/${session.key}/digest`)
          .then(r => r.ok ? r.json() : null)
          .then((d: { items?: DigestItem[] } | null) => { if (live) setItems(d?.items ?? null); })
          .catch(() => { if (live) setItems(null); })
          .finally(() => { digestPending = false; });
      }
      if (!mergesPending) {
        mergesPending = true;
        void forkMergeHistory(session.key)
          .then(data => { if (live) { setMerges(data.merges); setMergeError(""); } })
          .catch((e: Error) => { if (live) setMergeError(e.message); })
          .finally(() => { mergesPending = false; });
      }
      if (!checkpointsLoaded && !checkpointsPending) {
        checkpointsPending = true;
        void api.checkpoints(session.key)
          .then(data => { if (live) { setCheckpoints(data.checkpoints); checkpointsLoaded = true; } })
          .catch(() => undefined)
          .finally(() => { checkpointsPending = false; });
      }
    };
    load();
    const timer = setInterval(load, 10_000);
    document.addEventListener("visibilitychange", load);
    return () => { live = false; clearInterval(timer); document.removeEventListener("visibilitychange", load); };
  }, [session.key, active]);

  const byBucket = (bucket: string): DigestItem[] =>
    (items ?? []).filter((i) => i.bucket === bucket);
  // Fallback: the latest digest blob, rendered as ephemeral items.
  const fallback = (bucket: string): DigestItem[] =>
    ((p?.[bucket as keyof typeof p] as string[] | undefined) ?? []).map(
      (text, i) => ({
        id: `${bucket}-${i}`,
        bucket,
        text,
        status: "active" as const,
        created_at: session.progressAt ?? session.startedAt,
      }),
    );

  const hasArchive = items !== null && items.length > 0;
  const empty = !hasArchive && !p;

  return (
    <div className="rd-history">
      <div className="rd-history-meta">
        <span>
          started {started.toLocaleDateString()}{" "}
          {started.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}
        </span>
        {updated && <span>digest updated {updated}</span>}
        {hasArchive && <span>{items!.length} items archived</span>}
      </div>
      {empty ? (
        <p className="rd-panel-empty">
          No digest yet — it builds up automatically as the agent finishes its
          next few turns.
        </p>
      ) : (
        <>
          {p?.summary && <p className="rd-history-summary">{p.summary}</p>}
          {BUCKETS.map(([bucket, title, mark]) => (
            <Bucket
              key={bucket}
              title={title}
              mark={mark}
              items={hasArchive ? byBucket(bucket) : fallback(bucket)}
            />
          ))}
        </>
      )}
      {mergeError && <p className="rd-panel-empty">Merge history unavailable: {mergeError}</p>}
      {merges.length > 0 && <section className="rd-history-bucket"><h3>Fork merges</h3>
        {merges.map(row => <details key={row.id} className="rd-history-checkpoint">
          <summary>{new Date(row.createdAt).toLocaleString()} · {row.delivery}{row.statusAvailable === false ? " (last recorded)" : ""} · {row.parentDeleted ? "Parent deleted" : row.keepOpen ? "Child kept open" : row.childClosed ? "Child closed" : "Child closure pending"}</summary>
          {!row.keepOpen && !row.childClosed && splitSessionRef(session.key).key === row.child && <button className="rd-btn rd-btn-sm" onClick={async () => {
            try {
              await forkMergeService(session.key).send({ summary: row.summary, keepOpen: false, requestKey: row.id.split(":").at(-1)! });
              setMerges((await forkMergeHistory(session.key)).merges); setMergeError("");
            } catch (cause) { setMergeError((cause as Error).message); }
          }}>Finish closing child</button>}
          <p>Parent checkpoint: {row.checkpoint}</p><pre style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>{row.summary}</pre>
        </details>)}
      </section>}
      {checkpoints.length > 0 && (
        <section className="rd-history-bucket">
          <h3>Checkpoints</h3>
          {checkpoints.map((cp) => (
            <div key={cp.id} className="rd-history-checkpoint">
              <div className="rd-history-cp-head">
                <span className="rd-history-mark">⚑</span>
                {new Date(cp.created_at).toLocaleTimeString([], {
                  hour: "2-digit",
                  minute: "2-digit",
                })}{" "}
                · {cp.label}
              </div>
              {cp.summary && <p>{cp.summary}</p>}
            </div>
          ))}
        </section>
      )}
    </div>
  );
}

function Bucket({
  title,
  mark,
  items,
}: {
  title: string;
  mark: string;
  items: DigestItem[];
}) {
  if (items.length === 0) return null;
  // Active first; completed next_actions sink to the bottom, struck through —
  // they're history, not noise to delete.
  const ordered = [...items].sort((a, b) =>
    a.status === b.status
      ? a.created_at - b.created_at
      : a.status === "done"
        ? 1
        : -1,
  );
  return (
    <section className="rd-history-bucket">
      <h3>
        {title} <span className="rd-history-count">{items.length}</span>
      </h3>
      <ul className="rd-history-scroll">
        {ordered.map((item) => (
          <li key={item.id} className={item.status === "done" ? "done" : ""}>
            <span className="rd-history-mark">
              {item.status === "done" ? "✓" : mark}
            </span>
            <span className="rd-history-item-text">{item.text}</span>
            <span className="rd-history-when">{agoLabel(item.created_at)}</span>
          </li>
        ))}
      </ul>
    </section>
  );
}

function agoLabel(ts: number): string {
  const mins = Math.max(0, Math.round((Date.now() - ts) / 60_000));
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  if (mins < 60 * 24) return `${Math.round(mins / 60)}h ago`;
  return `${Math.round(mins / (60 * 24))}d ago`;
}
