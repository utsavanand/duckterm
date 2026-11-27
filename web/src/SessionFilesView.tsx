import { useEffect, useRef, useState } from "react";
import { api, type ArtifactKind } from "./api";
import { FileReader } from "./FileReader";
import { ARTIFACT_KINDS } from "./artifactKinds";
import { artifactFormat, filterArtifacts, type ArtifactFormat, type ArtifactSort } from "./artifactFilters";
import { sessionRef, splitSessionRef } from "./hostTransport";
import { FileRequestError, fileReason, fileSize, localFileSession, sessionFiles, sourceLabel, type DirectoryPage, type FileEntry, type RegisteredFile } from "./sessionFiles";
import type { SessionView } from "./types";
import "./sessionFiles.css";

type Directory = { page?: DirectoryPage; loading?: boolean; error?: string; stale?: boolean };
export type FileSelection = { kind: "file"; file: FileEntry } | { kind: "saved"; file: RegisteredFile };
type Selection = FileSelection;
type FilesProps = { session: SessionView; artifacts?: boolean; mode?: "code" | "artifacts"; browse?: boolean; artifactId?: string; onOpenArtifact?: (id: string) => void };
const order = (a: FileEntry, b: FileEntry) => Number(b.type === "directory") - Number(a.type === "directory") || a.name.localeCompare(b.name, undefined, { sensitivity: "base" });
const shortDate = (ts: number) => new Date(ts).toLocaleDateString(undefined, { month: "short", day: "numeric" });

export function SessionFilesView({ session, artifacts = true, mode = "code", browse = true, artifactId, onOpenArtifact }: FilesProps) {
  const [revision, setRevision] = useState(0);
  return <Files key={JSON.stringify([session.key, session.host, session.cwd, session.worktreePath, revision, artifacts, mode, browse])} session={session} artifacts={artifacts} mode={mode} browse={browse} artifactId={artifactId} onOpenArtifact={onOpenArtifact} refresh={() => setRevision(n => n + 1)} />;
}
function Files({ session, artifacts, mode, browse, artifactId, onOpenArtifact, refresh }: Required<Pick<FilesProps, "session" | "artifacts" | "mode" | "browse">> & Pick<FilesProps, "artifactId" | "onOpenArtifact"> & { refresh: () => void }) {
  const container = useRef<HTMLElement>(null);
  const [narrow, setNarrow] = useState(false);
  useEffect(() => {
    if (!container.current || typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(entries => setNarrow(entries[0].contentRect.width <= 650));
    observer.observe(container.current); return () => observer.disconnect();
  }, []);
  const local = browse && localFileSession(session);
  const savedKey = splitSessionRef(session.key).host !== "local" ? session.key : sessionRef(session.host || "local", session.key);
  const [directories, setDirectories] = useState<Record<string, Directory>>({});
  const dirs = useRef(directories); dirs.current = directories;
  const root = useRef("");
  const [rootPath, setRootPath] = useState("");
  const [unavailable, setUnavailable] = useState(local ? "" : "Remote — not browsable here");
  const [registry, setRegistry] = useState<RegisteredFile[] | null>(null);
  const [registryError, setRegistryError] = useState("");
  const [expanded, setExpanded] = useState(new Set<string>());
  const [format, setFormat] = useState<ArtifactFormat>("all");
  const [label, setLabel] = useState<ArtifactKind | "all">("all");
  const [sort, setSort] = useState<ArtifactSort>("recent");
  const [query, setQuery] = useState("");
  const [savedLimit, setSavedLimit] = useState(40);
  const [selection, setSelection] = useState<Selection | null>(null);
  const [reading, setReading] = useState(false);
  const openedTarget = useRef<string | undefined>();
  useEffect(() => {
    if (mode !== "artifacts" || !artifactId || openedTarget.current === artifactId) return;
    const target = registry?.find(a => a.id === artifactId);
    if (target) { openedTarget.current = artifactId; setSelection({ kind: "saved", file: target }); setReading(true); }
  }, [artifactId, registry, mode]);

  const alive = useRef(true);
  const requests = useRef(new Set<AbortController>());
  const pendingDirs = useRef(new Set<string>());
  const loadRef = useRef<(path: string) => void>(() => {});
  async function savedFallback() {
    if (!artifacts) return;
    try {
      const result = await api.artifacts(savedKey);
      if (alive.current) { setRegistry(result.artifacts.filter(a => !a.removed_at).map(a => ({ ...a, path: null, source_state: "unavailable", content_only: true }))); setRegistryError(""); }
    } catch (error) { if (alive.current) setRegistryError((error as Error).message); }
  }
  async function load(path: string, more = false) {
    if (!local || pendingDirs.current.has(path)) return;
    pendingDirs.current.add(path);
    const previous = dirs.current[path]?.page;
    const cursor = more ? previous?.next_cursor ?? undefined : undefined;
    const controller = new AbortController(); requests.current.add(controller);
    setDirectories(d => ({ ...d, [path]: { ...d[path], loading: true, error: "", stale: false } }));
    try {
      const result = await sessionFiles.list(session, path, cursor, controller.signal);
      if (!alive.current) return;
      if (!result.available) {
        if (path === ".") { setUnavailable(fileReason(result.reason)); setRegistry(artifacts ? result.artifacts?.filter(a => !a.removed_at) ?? null : null); setSelection(null); if (!result.artifacts) void savedFallback(); }
        setDirectories(d => ({ ...d, [path]: { error: fileReason(result.reason) } })); return;
      }
      if (root.current && result.root !== root.current) {
        setUnavailable("The session’s working directory changed. Refresh before browsing."); setSelection(null); setRegistry(null); setDirectories({}); return;
      }
      root.current = result.root; setRootPath(result.root);
      if (artifacts) { setRegistry(result.artifacts.filter(a => !a.removed_at)); setRegistryError(""); }
      const listed = artifacts ? result.entries : result.entries.map(entry => ({ ...entry, artifact: null }));
      if (cursor && result.next_cursor === cursor) throw new Error("The directory did not advance. Refresh before loading more.");
      const entries = more && previous ? [...new Map([...previous.entries, ...listed].map(e => [e.path, e])).values()].sort(order) : listed;
      setDirectories(d => ({ ...d, [path]: { page: { ...result, entries }, loading: false } }));
    } catch (error) {
      if (!alive.current || controller.signal.aborted) return;
      const stale = error instanceof FileRequestError && error.status === 409;
      setDirectories(d => ({ ...d, [path]: { page: stale ? undefined : d[path]?.page, error: (error as Error).message, stale } }));
      if (stale) { setSelection(null); setReading(false); }
      if (path === "." && !previous) { setUnavailable((error as Error).message); void savedFallback(); }
    } finally { pendingDirs.current.delete(path); requests.current.delete(controller); }
  }
  loadRef.current = path => { void load(path); };
  useEffect(() => {
    alive.current = true;
    let mounted = true;
    queueMicrotask(() => { if (mounted) { if (local) loadRef.current("."); else void savedFallback(); } });
    const controllers = requests.current;
    return () => { mounted = false; alive.current = false; controllers.forEach(c => c.abort()); };
    // The parent remounts this component for every session/host/root/revision change.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  function choose(next: Selection) { setSelection(next); setReading(true); }
  function toggle(entry: FileEntry) {
    setExpanded(old => { const next = new Set(old); if (next.has(entry.path)) next.delete(entry.path); else next.add(entry.path); return next; });
    if (!directories[entry.path]?.page && !directories[entry.path]?.loading) void load(entry.path);
  }
  function row(entry: FileEntry, depth = 0, fullPath = false) {
    const selected = selection?.kind === "file" && selection.file.path === entry.path;
    return <button className="rd-file-row" key={entry.path} aria-pressed={entry.type !== "directory" ? selected : undefined} aria-expanded={entry.type === "directory" ? expanded.has(entry.path) : undefined}
      title={entry.artifact ? `${entry.artifact.title} · ${entry.artifact.kind ?? "other"}` : entry.path} onClick={() => entry.type === "directory" ? toggle(entry) : choose({ kind: "file", file: entry })}>
      <span className="rd-file-name" style={{ paddingLeft: depth * 14 }}><span aria-hidden="true" className="rd-file-caret">{entry.type === "directory" ? expanded.has(entry.path) ? "▾" : "▸" : entry.type === "symlink" ? "↗" : "▤"}</span><span>{fullPath ? entry.path : entry.name}</span>{entry.artifact && <span className="rd-file-registered">ART<span className="rd-sr-only"> Registered artifact</span></span>}</span>
      <span className="rd-file-size">{fileSize(entry.size)}</span><time dateTime={new Date(entry.modified_at).toISOString()}>{shortDate(entry.modified_at)}</time>
    </button>;
  }
  function directory(path: string, depth = 0): React.ReactNode {
    const current = directories[path];
    return <div key={path}>
      {current?.page?.entries.map(entry => <div key={entry.path}>{row(entry, depth)}{entry.type === "directory" && expanded.has(entry.path) && directory(entry.path, depth + 1)}</div>)}
      {current?.loading && <p className="rd-files-note" role="status">Loading {path === "." ? "directory" : path}…</p>}
      {current?.error && <div className="rd-files-note" role="alert"><p>{current.error}</p><button className="rd-btn rd-btn-sm" onClick={() => { if (path === "." || current.stale) refresh(); else void load(path); }}>Refresh directory</button></div>}
      {current?.page && !current.loading && !current.page.entries.length && <p className="rd-files-note">{current.page.next_cursor ? "No visible files on this page. More entries are available." : "No visible files in this directory."}</p>}
      {current?.page?.next_cursor && <button className="rd-files-more" disabled={current.loading} onClick={() => void load(path, true)}>Load more in {path === "." ? "working directory" : path}</button>}
      {current?.page?.limit_reached && <p className="rd-files-warning" role="status">Listing incomplete: 10,000 entries examined in {path === "." ? "this directory" : path}. More files may exist.</p>}
    </div>;
  }
  const loaded = Object.values(directories).flatMap(d => d.page?.entries ?? []);
  const matches = (value: string) => value.toLocaleLowerCase().includes(query.toLocaleLowerCase());
  const visible = filterArtifacts(registry ?? [], { query, format, label, sort });
  const canBrowse = local && !unavailable;
  function registryRow(a: RegisteredFile) {
    const selected = selection?.kind === "saved" ? selection.file.id === a.id : selection?.file.artifact?.id === a.id;
    return <button key={a.id} className="rd-file-row rd-file-snapshot" aria-pressed={selected} title={a.source_path}
      aria-label={`${artifactFormat(a)} ${a.title}`} onClick={() => {
        if (a.source_state === "present" && a.path) choose({ kind: "file", file: { path: a.path, name: a.path.split("/").pop()!, type: "file", size: null, modified_at: 0, browsable: true, content_limit: 5242880, artifact: a } });
        else choose({ kind: "saved", file: a });
      }}><span><strong>{a.title}</strong><small>{a.source_path}</small><small>{artifactFormat(a)} · {ARTIFACT_KINDS[a.kind ?? "other"] ?? "Other"} · {shortDate(a.updated_at)}</small><small>{sourceLabel(a.source_state)}</small></span><span className="rd-file-size">{fileSize(a.size)}<small>saved</small></span></button>;
  }
  return <section ref={container} className={`rd-session-files${narrow ? " narrow" : ""}`} aria-label={mode === "code" ? "Code explorer" : "Session artifacts"}>
    <header className="rd-files-heading"><div><h2>{mode === "code" ? "Code explorer" : "Artifacts"} {mode === "code" && <span>Read-only</span>}</h2><p>{mode === "code" ? rootPath || session.worktreePath || session.cwd || "No working directory recorded" : `Saved outputs from ${session.label}`}</p></div><button className="rd-btn rd-btn-sm" onClick={refresh}>Refresh</button></header>
    <div className="rd-files-toolbar"><input aria-label={mode === "code" ? "Filter loaded files" : "Search artifacts"} placeholder={mode === "code" ? "Filter loaded files…" : "Search title, path or label…"} value={query} onChange={e => { setQuery(e.target.value); setSavedLimit(40); }} />
      {mode === "artifacts" && <><select aria-label="Artifact format" value={format} onChange={e => { setFormat(e.target.value as ArtifactFormat); setSavedLimit(40); }}><option value="all">All formats</option><option value="markdown">Markdown</option><option value="html">HTML</option><option value="react">React apps</option><option value="pdf">PDF</option><option value="other">Other files</option></select>
        <select aria-label="Artifact label" value={label} onChange={e => { setLabel(e.target.value as ArtifactKind | "all"); setSavedLimit(40); }}><option value="all">All labels</option>{Object.entries(ARTIFACT_KINDS).map(([kind, title]) => <option key={kind} value={kind}>{title}</option>)}</select>
        <select aria-label="Sort artifacts" value={sort} onChange={e => setSort(e.target.value as ArtifactSort)}><option value="recent">Most recent</option><option value="oldest">Oldest first</option><option value="name">Name A–Z</option></select></>}
    </div>
    {mode === "code" && !canBrowse ? <div className="rd-files-unavailable"><h3>{unavailable}</h3><p>{!local ? "These files live on another machine. No local files are shown in their place." : "No directory is shown."}</p>{artifacts && <p>Saved outputs remain available in the Artifacts tab.</p>}</div> : <div className={`rd-files-layout${reading ? " reading" : ""}`}>
      <nav className="rd-files-tree" aria-label={mode === "code" ? "Working directory" : "Artifact list"}><div className="rd-files-columns">{mode === "code" ? <><span>Name</span><span>Size</span><span>Modified</span></> : <span>{visible.length} artifacts</span>}</div><div className="rd-files-scroll">
        {mode === "code" ? query ? <>{loaded.filter(e => matches(e.path)).map(e => row(e, 0, true))}{!loaded.some(e => matches(e.path)) && <p className="rd-files-note">No matching loaded files. Unexpanded folders have not been searched.</p>}</> : directory(".") : <>
          {registryError && <p className="rd-files-note" role="alert">Saved artifact metadata unavailable: {registryError}</p>}
          {registry === null && !registryError && <p className="rd-files-note" role="status">Loading saved artifacts…</p>}
          {visible.slice(0, savedLimit).map(registryRow)}
          {registry && !visible.length && <p className="rd-files-note">No matching artifacts.</p>}
          {visible.length > savedLimit && <button className="rd-files-more" onClick={() => setSavedLimit(n => n + 40)}>Show more artifacts</button>}
        </>}
      </div><p className="rd-files-footnote">{mode === "code" ? "Ignore rules applied · expand folders to load them · filter searches loaded paths only" : "Registered files and saved copies · sizes refer to the saved version"}</p></nav>
      {selection ? <FileReader key={selection.kind + ":" + (selection.kind === "file" ? selection.file.path : selection.file.id)} session={session} savedKey={savedKey} selection={selection} back={() => setReading(false)} saved={a => choose({ kind: "saved", file: a })} onOpenArtifact={mode === "code" ? onOpenArtifact : undefined} onMetadata={a => setRegistry(rows => rows?.map(row => row.id === a.id ? { ...row, ...a } : row) ?? null)} /> : <section className="rd-file-reader"><p className="rd-files-note">{mode === "code" ? "Choose a file to read it." : "Choose an artifact to read it."}</p></section>}
    </div>}
  </section>;
}
