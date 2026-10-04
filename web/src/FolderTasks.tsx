import { useEffect, useState } from "react";
import { authHeaders } from "./api";
import { WidgetType } from "./Widgets";
import "./folderTasks.css";
export interface FolderTask { id: string; folder: string; title: string; owner_session: string; owner_name: string | null; owner_deleted: boolean; activity: string | null; status: "in_progress" | "done" | "parked"; created_at: number; updated_at: number; note: string; }
export function tasksWidget(onOpen: (key: string) => void): WidgetType {
  return { type: "folder-tasks", title: "Tasks", slots: ["folder", "oracle"], streams: [], render: ({ params }) => <FolderTasks folder={params.folder} onOpen={onOpen} /> };
}
export function FolderTasks({ folder, onOpen }: { folder?: string; onOpen: (key: string) => void }) {
  const [tasks, setTasks] = useState<FolderTask[] | null>(null), [error, setError] = useState("");
  const [filter, setFilter] = useState("open"), [selectedFolder, setSelectedFolder] = useState("");
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    let active = true, running = false;
    async function refresh() {
      if (!active || running || document.visibilityState === "hidden") return;
      running = true;
      try {
        const response = await fetch("/tasks", { headers: authHeaders(), cache: "no-store" });
        const data = await response.json();
        if (!response.ok) throw new Error(data.error ?? "Tasks are unavailable");
        if (active) { setTasks(data.tasks); setError(""); }
      } catch (cause) { if (active) setError((cause as Error).message); }
      finally { running = false; }
    }
    void refresh(); const timer = setInterval(() => void refresh(), 10000);
    const changed = () => void refresh();
    document.addEventListener("visibilitychange", changed); window.addEventListener("folder-tasks-refresh", changed);
    return () => { active = false; clearInterval(timer); document.removeEventListener("visibilitychange", changed); window.removeEventListener("folder-tasks-refresh", changed); };
  }, [retry]);
  const scope = folder ?? selectedFolder;
  const scoped = (tasks ?? []).filter(task => !scope || task.folder === scope || task.folder.startsWith(scope + "/"));
  const ordered = scoped.filter(task => filter === "all" || filter === "open" ? filter === "all" || task.status !== "done" : task.status === filter)
    .sort((a, b) => ["in_progress", "parked", "done"].indexOf(a.status) - ["in_progress", "parked", "done"].indexOf(b.status) || a.created_at - b.created_at);
  return <div className="rd-folder-tasks">
    {tasks && <div className="rd-task-counts"><span><strong>{scoped.filter(t => t.status === "in_progress").length}</strong> in progress</span><span><strong>{scoped.filter(t => t.status === "parked").length}</strong> parked</span></div>}
    <p>In progress first · oldest started first</p>
    {!folder && <select aria-label="Task folder" value={selectedFolder} onChange={e => setSelectedFolder(e.target.value)}><option value="">All folders</option>{[...new Set((tasks ?? []).map(t => t.folder))].sort().map(f => <option key={f}>{f}</option>)}</select>}
    <select aria-label="Filter tasks" value={filter} onChange={e => setFilter(e.target.value)}><option value="open">In progress + parked</option><option value="in_progress">In progress</option><option value="parked">Parked</option><option value="done">Done</option><option value="all">All tasks</option></select>
    {error && <p role="alert">{error}{tasks && " · Showing last loaded tasks."} <button onClick={() => setRetry(n => n + 1)}>Retry</button></p>}
    {!tasks && !error && <p role="status">Loading tasks…</p>}
    {tasks && !ordered.length && <p>No tasks in this view. Tasks start with an explicit assignment or <code>task start</code>.</p>}
    <div className="rd-task-list">{ordered.map(task => <article key={task.id}>
      <h4>{task.title}</h4><div className="rd-task-owner">{task.owner_deleted ? <span>Session deleted</span> : <button onClick={() => onOpen(task.owner_session)}>{task.owner_name || task.owner_session} ↗</button>}{!folder && <small>{task.folder}</small>}</div>
      <p className="rd-task-doing"><small>Doing now</small>{task.activity || "No activity reported"}</p>
      {task.note && <p>{task.note}</p>}
      <footer><span className={`rd-task-status ${task.status}`}>{task.status.replace("_", " ")}</span><time dateTime={new Date(task.created_at).toISOString()} title={new Date(task.created_at).toLocaleString()}>{age(task.created_at)}</time></footer>
    </article>)}</div>
    <p>Done needs no evidence. Parked tasks send no reminders.</p>
  </div>;
}
function age(at: number) { const minutes = Math.max(0, Math.floor((Date.now() - at) / 60000)); return minutes < 60 ? `${minutes}m` : minutes < 1440 ? `${Math.floor(minutes / 60)}h` : `${Math.floor(minutes / 1440)}d`; }
