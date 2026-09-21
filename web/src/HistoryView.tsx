import { useEffect, useState } from "react";
import { api, CheckpointRecord } from "./api";
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

export function HistoryView({ session }: { session: SessionView }) {
  const p = session.progress;
  const started = new Date(session.startedAt);
  const updated = session.progressAt ? agoLabel(session.progressAt) : null;
  const [items, setItems] = useState<DigestItem[] | null>(null);
  const [checkpoints, setCheckpoints] = useState<CheckpointRecord[]>([]);

  useEffect(() => {
    setItems(null);
    setCheckpoints([]);
    const load = () =>
      fetch(`/sessions/${session.key}/digest`)
        .then((r) => (r.ok ? r.json() : null))
        .then((d: { items?: DigestItem[] } | null) =>
          setItems(d?.items ?? null),
        )
        .catch(() => setItems(null));
    load();
    const t = setInterval(load, 10_000);
    api
      .checkpoints(session.key)
      .then((d) => setCheckpoints(d.checkpoints))
      .catch(() => undefined);
    return () => clearInterval(t);
  }, [session.key]);

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
