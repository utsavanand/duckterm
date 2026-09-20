import { SessionView } from "./types";

// Middle-pane History tab: the session's running digest in full — when it
// started, what got delivered, what was learned, what's next. The digest is
// regenerated server-side every few turns and stored on the session row, so
// it's always available (including after restarts).
export function HistoryView({ session }: { session: SessionView }) {
  const p = session.progress;
  const started = new Date(session.startedAt);
  const updated = session.progressAt ? agoLabel(session.progressAt) : null;

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
          No digest yet — it builds up automatically as the agent works
          (regenerated every few turns).
        </p>
      ) : (
        <>
          {p.summary && <p className="rd-history-summary">{p.summary}</p>}
          <Bucket title="Delivered" items={p.deliverables} mark="✓" />
          <Bucket title="Learnings" items={p.learnings} mark="◆" />
          <Bucket title="Next actions" items={p.next_actions} mark="→" />
        </>
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
