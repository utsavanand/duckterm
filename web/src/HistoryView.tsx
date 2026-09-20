import { useEffect, useState } from "react";
import { api, CheckpointRecord } from "./api";
import { SessionView } from "./types";

// Middle-pane History tab: the session's running digest in full — when it
// started, what got delivered, what was learned, what's next — plus the
// checkpoint timeline. The digest is regenerated server-side every few turns
// and stored on the session row, so it survives restarts.
export function HistoryView({ session }: { session: SessionView }) {
  const p = session.progress;
  const started = new Date(session.startedAt);
  const updated = session.progressAt ? agoLabel(session.progressAt) : null;
  const [checkpoints, setCheckpoints] = useState<CheckpointRecord[]>([]);
  useEffect(() => {
    setCheckpoints([]);
    api
      .checkpoints(session.key)
      .then((d) => setCheckpoints(d.checkpoints))
      .catch(() => undefined);
  }, [session.key]);

  return (
    <div className="rd-history">
      <div className="rd-history-meta">
        <span>
          started {started.toLocaleDateString()}{" "}
          {started.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}
        </span>
        {updated && <span>digest updated {updated}</span>}
      </div>
      {!p ? (
        <p className="rd-panel-empty">
          No digest yet — it builds up automatically as the agent finishes its
          next few turns.
        </p>
      ) : (
        <>
          {p.summary && <p className="rd-history-summary">{p.summary}</p>}
          <Bucket title="Delivered" items={p.deliverables} mark="✓" />
          <Bucket title="Task learnings" items={p.learnings} mark="◆" />
          <Bucket
            title="Working together"
            items={p.user_learnings}
            mark="◇"
          />
          <Bucket title="Next actions" items={p.next_actions} mark="→" />
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
  items,
  mark,
}: {
  title: string;
  items: string[];
  mark: string;
}) {
  if (items.length === 0) return null;
  return (
    <section className="rd-history-bucket">
      <h3>{title}</h3>
      <ul>
        {items.map((item, i) => (
          <li key={i}>
            <span className="rd-history-mark">{mark}</span>
            {item}
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
  return `${Math.round(mins / 60)}h ago`;
}
