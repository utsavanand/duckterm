import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, type ArchMap, type Release, type ReleaseCompare, type ReviewHeader } from "../api";
import { ArchMapView } from "./ArchMap";

const POLL_MS = 4000;

function day(at: number): string {
  return new Date(at * 1000).toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" });
}

export function HistoryPane({ header, active }: { header: ReviewHeader; active: boolean }) {
  const { repo, ref } = header;
  const [releases, setReleases] = useState<Release[] | null>(null);
  const [status, setStatus] = useState<Record<string, string>>({});
  const [touched, setTouched] = useState<string[]>([]);
  const [shipped, setShipped] = useState<string | null>(null);
  const [onlyTouched, setOnlyTouched] = useState(false);
  const [selected, setSelected] = useState<string | null>(null);
  const [other, setOther] = useState<string | null>(null);
  const [maps, setMaps] = useState<Record<string, ArchMap>>({});
  const [comparison, setComparison] = useState<ReleaseCompare | null>(null);
  const [error, setError] = useState("");
  const alive = useRef(true);
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);
  const load = useCallback(async () => {
    try {
      const result = await api.releases(repo, ref);
      if (!alive.current) return;
      setReleases(result.releases); setStatus(result.maps); setTouched(result.touched); setShipped(result.shipped);
      setSelected(current => current ?? result.releases[result.releases.length - 1]?.tag ?? null);
      setError("");
    } catch (cause) { if (alive.current) setError((cause as Error).message); }
  }, [repo, ref]);
  useEffect(() => { void load(); }, [load]);
  // Maps fill in behind, newest first; poll while any is still pending.
  const pending = Object.values(status).some(value => value === "pending" || value === "missing");
  useEffect(() => {
    if (!active || !pending) return;
    const timer = setInterval(() => void load(), POLL_MS);
    return () => clearInterval(timer);
  }, [active, pending, load]);
  const fetchMap = useCallback(async (tag: string) => {
    try {
      const map = await api.archmap(repo, ref, tag);
      if (alive.current) setMaps(previous => ({ ...previous, [tag]: map }));
    } catch (cause) { if (alive.current) setError((cause as Error).message); }
  }, [repo, ref]);
  useEffect(() => { if (selected && !maps[selected]) void fetchMap(selected); }, [selected, maps, fetchMap]);
  useEffect(() => { if (other && !maps[other]) void fetchMap(other); }, [other, maps, fetchMap]);
  useEffect(() => {
    if (!selected || !other || selected === other) { setComparison(null); return; }
    const [older, newer] = [selected, other].sort((a, b) => (releases?.findIndex(r => r.tag === a) ?? 0) - (releases?.findIndex(r => r.tag === b) ?? 0));
    let cancelled = false;
    api.compareReleases(repo, ref, older, newer).then(result => { if (!cancelled && alive.current) setComparison(result); }).catch((cause: Error) => { if (!cancelled && alive.current) setError(cause.message); });
    return () => { cancelled = true; };
  }, [selected, other, repo, ref, releases]);

  const shown = useMemo(() => (releases ?? []).filter(release => !onlyTouched || touched.includes(release.tag)), [releases, onlyTouched, touched]);
  const current = selected ? maps[selected] : undefined;
  const release = releases?.find(r => r.tag === selected) ?? null;
  function pick(tag: string, withShift: boolean) {
    if (withShift && selected && tag !== selected) { setOther(tag); return; }
    setSelected(tag); setOther(null);
  }
  function previousOf(tag: string): string | null {
    const index = (releases ?? []).findIndex(r => r.tag === tag);
    return index > 0 ? releases![index - 1].tag : null;
  }
  return <div className="rd-history">
    <div className="rd-history-toolbar">
      <span className="rd-card-meta">{releases ? `${releases.length} releases` : "Reading tags…"}{shipped && ` · this change ${header.pr?.state === "merged" ? "shipped in" : "will ship after"} ${shipped}`}</span>
      <label><input type="checkbox" checked={onlyTouched} onChange={event => setOnlyTouched(event.target.checked)} /> Only releases that touched this PR's files ({touched.length})</label>
      {selected && previousOf(selected) && <button className="rd-btn rd-btn-ghost rd-btn-sm" onClick={() => setOther(previousOf(selected))}>Compare with previous</button>}
      {other && <button className="rd-btn rd-btn-ghost rd-btn-sm" onClick={() => setOther(null)}>Clear comparison</button>}
    </div>
    {error && <p className="rd-prs-note" role="alert">{error}</p>}
    {releases && releases.length === 0 && <p className="rd-prs-empty">No release tags (v*) in this repository yet.</p>}
    {shown.length > 0 && <ol className="rd-timeline" aria-label="Releases">
      {shown.map(item => <li key={item.tag} className={`${item.tag === selected ? "selected" : ""}${item.tag === other ? " other" : ""}${item.tag === shipped ? " shipped" : ""}`}>
        <button onClick={event => pick(item.tag, event.shiftKey)} title={`${item.subject || item.tag} · shift-click to compare`} aria-pressed={item.tag === selected || item.tag === other}>
          <span className="rd-timeline-dot" data-status={status[item.tag] ?? "missing"} />
          <span className="rd-timeline-tag">{item.tag}</span>
          <span className="rd-timeline-date">{day(item.at)}</span>
          <span className="rd-timeline-merges">{item.merges.length} {item.merges.length === 1 ? "merge" : "merges"}</span>
        </button>
      </li>)}
    </ol>}
    {release && <section className="rd-history-detail">
      <header>
        <h3>{release.tag}{other && <> ↔ {other}</>}</h3>
        <p className="rd-card-meta">{day(release.at)}{release.subject ? ` · ${release.subject}` : ""}{status[release.tag] === "pending" ? " · map being written" : ""}</p>
      </header>
      {current?.status === "omitted" && <p className="rd-prs-note">Module map omitted: {current.reason}</p>}
      {current?.status === "ready" && <ArchMapView map={current} comparison={comparison} otherMap={other ? maps[other] : undefined} />}
      {(!current || current.status === "pending") && <p className="rd-prs-empty" role="status">Writing the module map for {release.tag}…</p>}
      {comparison && <p className="rd-card-meta">{comparison.files_changed} files changed between {comparison.older} and {comparison.newer} · {Object.values(comparison.marks).filter(m => m === "added").length} modules added · {Object.values(comparison.marks).filter(m => m === "changed").length} changed · {Object.values(comparison.marks).filter(m => m === "removed").length} removed</p>}
      {release.merges.length > 0 && <>
        <h4>Merged in this release</h4>
        <ul className="rd-card-list">{release.merges.map(merge => <li key={merge.sha}><code>{merge.sha}</code> {merge.subject}{merge.pr && header.github && <> · <a href={`https://github.com/${header.github}/pull/${merge.pr}`} target="_blank" rel="noreferrer">#{merge.pr}</a></>}</li>)}</ul>
      </>}
    </section>}
  </div>;
}
