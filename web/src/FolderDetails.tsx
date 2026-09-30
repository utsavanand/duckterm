import { useEffect, useRef, useState } from "react";
import { api, FolderStats } from "./api";
import { ARTIFACT_KINDS } from "./artifactKinds";
import { compact } from "./tower";
import { Widgets, WidgetType } from "./Widgets";
import { WidgetStreams } from "./widgetStreams";
export interface FolderActions { onOpenSession?: (key: string) => void; onGrid?: () => void; onMessage?: () => void; onNewSession?: () => void; }
export function FolderDetails({ folder, onClose, onOpenSession, onGrid, onMessage, onNewSession }: FolderActions & { folder: string; onClose: () => void }) {
  const [source] = useState(() => new WidgetStreams());
  const [retry, setRetry] = useState(0), [error, setError] = useState("");
  const [updated, setUpdated] = useState<number | null>(null);
  const open = useRef(onOpenSession); open.current = onOpenSession;
  const [registry] = useState<WidgetType[]>(() => [
    { type: "folder-stats", title: "Activity", slots: ["folder"], streams: ["folder-stats"], render: ({ streams }) => <Activity stats={streams["folder-stats"].status === "ready" ? streams["folder-stats"].value as FolderStats : null} onOpen={key => open.current?.(key)} /> },
    { type: "artifacts-by-kind", title: "Artifacts by kind", slots: ["folder"], streams: ["artifacts-by-kind"], render: ({ streams }) => {
      const data = streams["artifacts-by-kind"].status === "ready" ? streams["artifacts-by-kind"].value as FolderStats["artifacts"] : null;
      return data && <div className="rd-folder-kind-counts">{Object.entries(ARTIFACT_KINDS).map(([key, label]) => <div key={key}><span>{label}</span><strong>{data.by_kind[key as keyof typeof ARTIFACT_KINDS] ?? 0}</strong></div>)}<p>{data.available} available · {data.removed} removed<br />Removed files are included in kind counts.</p></div>;
    } },
  ]);
  useEffect(() => {
    const refresh = () => setRetry(n => n + 1); window.addEventListener("folder-stats-refresh", refresh);
    return () => window.removeEventListener("folder-stats-refresh", refresh);
  }, []);
  useEffect(() => {
    let stale = false; setError("");
    for (const name of ["folder-stats", "artifacts-by-kind"]) source.emit(name, { status: "loading" });
    void api.folderStats(folder).then(data => {
      if (stale) return; setUpdated(data.updated_at);
      source.emit("folder-stats", { status: "ready", value: data });
      source.emit("artifacts-by-kind", { status: "ready", value: data.artifacts });
    }).catch((e: Error) => { if (!stale) { setError(e.message); for (const name of ["folder-stats", "artifacts-by-kind"]) source.emit(name, { status: "unavailable", message: e.message }); } });
    return () => { stale = true; };
  }, [folder, retry, source]);
  return <aside className="rd-folder-details" aria-label="Folder details"><header><h2>Folder details</h2><button className="rd-btn rd-btn-ghost rd-btn-sm" onClick={onClose} aria-label="Close folder details">×</button></header>
    <small>Includes this folder and subfolders · Local sessions</small>
    {error && <p role="alert">{error}</p>}
    <Widgets key={folder} surface={`folder:${folder}`} slot="folder" registry={registry} source={source} />
    <section className="rd-folder-actions" aria-label="Folder actions">{onGrid && <button onClick={onGrid}>Grid view</button>}{onMessage && <button onClick={onMessage}>Message folder</button>}{onNewSession && <button onClick={onNewSession}>New session here</button>}</section>
    <footer><button className="rd-btn rd-btn-ghost rd-btn-sm" onClick={() => setRetry(n => n + 1)}>Refresh details</button>{updated && <small>Updated {new Date(updated).toLocaleTimeString()}</small>}</footer>
  </aside>;
}
function Activity({ stats, onOpen }: { stats: FolderStats | null; onOpen: (key: string) => void }) {
  const [days, setDays] = useState("1");
  if (!stats) return null;
  const period = stats.periods[days];
  const total = Object.values(stats.sessions).reduce((sum, count) => sum + count, 0);
  return <div className="rd-folder-activity"><strong>{total} {total === 1 ? "session" : "sessions"}</strong><p>{stats.sessions.busy} busy · {stats.sessions.idle} idle · {stats.sessions.waiting} waiting</p>
    {!!stats.waiting.length && <section><h4>Waiting on you</h4>{stats.waiting.map(session => <button key={session.key} onClick={() => onOpen(session.key)}>{session.name} ↗</button>)}</section>}
    <div className="rd-folder-period" role="group" aria-label="Activity period"><button aria-pressed={days === "1"} onClick={() => setDays("1")}>Today</button><button aria-pressed={days === "7"} onClick={() => setDays("7")}>7 days</button><small>UTC</small></div>
    <dl><dt>Tokens</dt><dd>{compact(period.tokens)}</dd><dt>Messages sent</dt><dd>{period.sent}</dd><dt>Replies</dt><dd>{period.answered}</dd></dl><p>Messages sent to or from this folder. Token usage excludes test sessions and transcripts that cannot be matched.</p>
  </div>;
}
