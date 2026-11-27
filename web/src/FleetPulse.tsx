import { useEffect, useState } from "react";
import { api, PulseEvent, RelayNote } from "./api";
import { ago } from "./tower";

type Kind = "needs" | "pr" | "artifact";
const FILTERS: [Kind, string][] = [["needs", "Needs you"], ["pr", "PRs & releases"], ["artifact", "Artifacts"]];
const POLL_MS = 30_000;

// The Fleet monitor's Pulse rail: only what's worth the owner's attention.
// Needs-you items come from the open relay notes, so an answered one drops
// out; artifacts, PR merges and releases come from /fleet/pulse.
export function FleetPulse({ needs, now, onNeeds, onOpenTerminal }: {
  needs: RelayNote[];
  now: number;
  onNeeds: () => void;
  onOpenTerminal: (key: string) => void;
}) {
  const [events, setEvents] = useState<PulseEvent[]>([]);
  const [error, setError] = useState("");
  const [shown, setShown] = useState<Set<Kind>>(() => new Set(FILTERS.map(([k]) => k)));

  useEffect(() => {
    let live = true;
    const load = () => api.fleetPulse()
      .then((r) => { if (live) { setEvents(r.events); setError(""); } })
      .catch((e: Error) => { if (live) setError(e.message); });
    void load();
    const timer = setInterval(load, POLL_MS);
    return () => { live = false; clearInterval(timer); };
  }, []);

  const rows: (PulseEvent & { note?: RelayNote })[] = [
    ...needs.map((n) => ({ kind: "needs" as const, at: n.created_at, title: n.question ?? n.questions?.[0]?.question ?? (n.detail ? `Wants to run ${n.detail}` : "Needs you"), name: n.name, session_key: n.session_key, note: n })),
    ...events,
  ]
    .filter((e) => shown.has(e.kind === "release" ? "pr" : e.kind))
    .sort((a, b) => b.at - a.at);

  const toggle = (k: Kind) => setShown((s) => { const next = new Set(s); if (next.has(k)) next.delete(k); else next.add(k); return next; });

  return (
    <aside className="rd-pulse" aria-label="Pulse">
      <header><span className="rd-pulse-beat" aria-hidden="true" /><h2>Pulse</h2><small>{rows.length} {rows.length === 1 ? "event" : "events"} · 7 days</small></header>
      <div className="rd-pulse-filters">
        {FILTERS.map(([k, label]) => <button key={k} aria-pressed={shown.has(k)} onClick={() => toggle(k)}>{label}</button>)}
      </div>
      {error && <p className="rd-oracle-error" role="alert">Pulse could not load: {error}</p>}
      <ol className="rd-pulse-feed">
        {rows.length === 0 && !error && <li className="rd-pulse-empty">Nothing in the last 7 days.</li>}
        {rows.map((e, i) => (
          <li key={`${e.kind}-${e.at}-${i}`} className={`rd-pulse-ev ${e.kind}`}>
            <span className="rd-pulse-dot" aria-hidden="true" />
            <div>
              <div className="rd-pulse-what">{line(e, e.session_key ? () => onOpenTerminal(e.session_key!) : undefined)}</div>
              <div className="rd-pulse-when">{e.kind === "needs" ? e.title : e.kind === "release" ? e.detail || e.repo : e.title}{" · "}{ago(now - e.at)} ago</div>
              {e.kind === "needs" && <button className="rd-pulse-act" onClick={onNeeds}>Answer</button>}
            </div>
          </li>
        ))}
      </ol>
    </aside>
  );
}

// The agent's name opens its terminal; a repository name is plain text.
function line(e: PulseEvent, open?: () => void) {
  const who = open ? <button className="rd-pulse-who" title={`Open ${e.name}'s terminal`} onClick={open}>{e.name}</button> : <b>{e.name ?? e.repo}</b>;
  if (e.kind === "needs") return <>{who} needs you</>;
  if (e.kind === "artifact") return <>{who} made {(e.count ?? 1) > 1 ? `${e.count} artifacts` : "an artifact"}</>;
  if (e.kind === "release") return <><b>{e.repo}</b> released {e.title}</>;
  return <>{who} merged a PR{e.name && e.repo ? <> in {e.repo}</> : null}</>;
}
