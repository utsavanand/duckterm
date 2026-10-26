import { useCallback, useEffect, useRef, useState } from "react";
import { api, type PrListResult, type PrRow } from "../api";
import { approvalLabel } from "./ApproveDialog";
import "./review.css";

export type PrScope = { kind: "session"; key: string; name: string } | { kind: "folder"; folder: string };
type Filter = "all" | "needs_you" | "open" | "merged";
const POLL_MS = 60_000;
const FILTERS: { id: Filter; label: string }[] = [
  { id: "all", label: "All" },
  { id: "needs_you", label: "Needs you" },
  { id: "open", label: "Open" },
  { id: "merged", label: "Merged" },
];

export function ago(seconds: number | null, now = Date.now()): string {
  if (!seconds) return "";
  const delta = Math.max(0, Math.round(now / 1000 - seconds));
  if (delta < 60) return "just now";
  if (delta < 3600) return `${Math.floor(delta / 60)}m ago`;
  if (delta < 86400) return `${Math.floor(delta / 3600)}h ago`;
  return `${Math.floor(delta / 86400)}d ago`;
}

function matches(row: PrRow, filter: Filter): boolean {
  if (filter === "all") return true;
  if (filter === "needs_you") return row.needs_you;
  if (filter === "merged") return row.status === "merged";
  return row.pr?.state === "open" || row.status === "pushed" || row.status === "unpushed";
}

function from(row: PrRow): string {
  if (row.session) return row.session.name;
  return row.author ? `@${row.author}` : "owner";
}

export function PrList({ scope, active, onOpenSession, onReview }: {
  scope: PrScope;
  active: boolean;
  onOpenSession?: (key: string) => void;
  onReview?: (row: PrRow) => void;
}) {
  const [data, setData] = useState<PrListResult | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [filter, setFilter] = useState<Filter>("all");
  const alive = useRef(true);
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);
  const load = useCallback(async (refresh: boolean) => {
    setBusy(true);
    try {
      const result = scope.kind === "session" ? await api.sessionPrs(scope.key, refresh) : await api.folderPrs(scope.folder, refresh);
      if (alive.current) { setData(result); setError(""); }
    } catch (cause) {
      if (alive.current) setError((cause as Error).message);
    } finally {
      if (alive.current) setBusy(false);
    }
  }, [scope]);
  useEffect(() => { void load(false); }, [load]);
  // GitHub is polled only while this list is on screen (spec §3): the view is
  // active and the window is not hidden. The 60 s rate-limit budget assumes it.
  const [visible, setVisible] = useState(() => document.visibilityState !== "hidden");
  useEffect(() => {
    const onChange = () => setVisible(document.visibilityState !== "hidden");
    document.addEventListener("visibilitychange", onChange);
    return () => document.removeEventListener("visibilitychange", onChange);
  }, []);
  const resumed = useRef(false);
  useEffect(() => {
    if (!active || !visible) { resumed.current = true; return; }
    if (resumed.current) { resumed.current = false; void load(false); }
    const timer = setInterval(() => void load(false), POLL_MS);
    return () => clearInterval(timer);
  }, [active, visible, load]);

  const rows = (data?.rows ?? []).filter(row => matches(row, filter));
  const repos = data?.repos ?? [];
  const problems = repos.filter(repo => repo.error || !repo.connected);
  const fetched = repos.reduce((latest, repo) => Math.max(latest, repo.fetched_at), 0);
  const counts = Object.fromEntries(FILTERS.map(f => [f.id, (data?.rows ?? []).filter(row => matches(row, f.id)).length])) as Record<Filter, number>;
  return <section className="rd-prs" aria-label="Pull requests">
    <div className="rd-prs-head">
      <div>
        <h2>Pull requests</h2>
        <small>{scope.kind === "session" ? `Branches pushed from ${scope.name}` : `Across ${scope.folder} and its subfolders`}{fetched ? ` · updated ${ago(fetched)}` : ""}</small>
      </div>
      <div className="rd-prs-filters" role="group" aria-label="Filter pull requests">
        {FILTERS.map(f => <button key={f.id} aria-pressed={filter === f.id} onClick={() => setFilter(f.id)}>{f.label}{data ? ` ${counts[f.id]}` : ""}</button>)}
        <button className="rd-btn rd-btn-ghost rd-btn-sm" disabled={busy} onClick={() => void load(true)}>{busy ? "Refreshing…" : "Refresh"}</button>
      </div>
    </div>
    {error && <p className="rd-prs-note" role="alert">{error}</p>}
    {problems.map(repo => <p key={repo.path} className="rd-prs-note" role="status"><strong>{repo.name}</strong>{repo.error ?? "GitHub is not connected, so only local branch facts are shown."}</p>)}
    {!data && !error && <p className="rd-prs-empty" role="status">Reading branches and pull requests…</p>}
    {data && rows.length === 0 && <p className="rd-prs-empty">{data.rows.length === 0
      ? (scope.kind === "session" ? "No pull requests yet. When this session commits on its branch, it shows here with its push and PR status." : "No pull requests from this folder's sessions yet.")
      : "Nothing matches this filter."}</p>}
    {rows.length > 0 && <table className="rd-prs-table">
      <thead><tr>
        {scope.kind === "folder" && <th>From</th>}
        <th>PR</th><th>Title</th><th>Status</th><th>Checks</th><th>Size</th><th>Updated</th><th><span className="sr-only">Actions</span></th>
      </tr></thead>
      <tbody>{rows.map(row => <Row key={`${row.repo}:${row.branch}`} row={row} folder={scope.kind === "folder"} onOpenSession={onOpenSession} onReview={onReview} />)}</tbody>
    </table>}
  </section>;
}

function Row({ row, folder, onOpenSession, onReview }: { row: PrRow; folder: boolean; onOpenSession?: (key: string) => void; onReview?: (row: PrRow) => void }) {
  const pr = row.pr;
  const checks = row.checks;
  return <tr className={row.needs_you ? "needs-you" : undefined}>
    {folder && <td className="rd-prs-from">{row.session && onOpenSession ? <button onClick={() => onOpenSession(row.session!.key)}>{from(row)}</button> : from(row)}</td>}
    <td className="rd-prs-number">{pr ? `#${pr.number}` : "—"}</td>
    <td>
      <div className="rd-prs-title">{pr ? <a href={pr.url} target="_blank" rel="noreferrer">{pr.title}</a> : row.branch}</div>
      <span className="rd-prs-branch">{row.repo_name} · {row.branch} → {row.base}{row.unpushed_commits ? ` · ${row.unpushed_commits} unpushed` : ""}</span>
    </td>
    <td><span className={`rd-pr-status is-${row.status}`}>{row.label}</span>{row.approval && <small className="rd-pr-approval">{approvalLabel(row.approval)}</small>}</td>
    <td className="rd-prs-checks">{checks && checks.total ? <>
      {checks.failed ? <span className="failed">{checks.failed} failed</span> : checks.pending ? <span className="pending">{checks.pending} running</span> : <span>{checks.passed}/{checks.total} passed</span>}
    </> : "—"}</td>
    <td className="rd-prs-size">{row.size ? <><span className="add">+{row.size.additions}</span> <span className="del">−{row.size.deletions}</span><small>{row.size.files} {row.size.files === 1 ? "file" : "files"}{row.ahead ? ` · ${row.ahead} ${row.ahead === 1 ? "commit" : "commits"}` : ""}</small></> : "—"}</td>
    <td className="rd-prs-when">{ago(row.updated_at)}</td>
    <td>
      {onReview && <button className="rd-btn rd-btn-primary rd-btn-sm" onClick={() => onReview(row)}>Review</button>}
      {pr && !onReview && <a className="rd-prs-link primary" href={pr.url} target="_blank" rel="noreferrer">Review on GitHub</a>}
      {!pr && row.compare_url && <a className="rd-prs-link" href={row.compare_url} target="_blank" rel="noreferrer">Open PR…</a>}
    </td>
  </tr>;
}
