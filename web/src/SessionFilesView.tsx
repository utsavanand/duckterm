import { useEffect, useMemo, useRef, useState } from "react";
import { api, type ArtifactContent } from "./api";
import { ArtifactPreview } from "./ArtifactsView";
import { ArtifactFeedback, type FeedbackTarget } from "./ArtifactFeedback";
import { useArtifactExpansion } from "./useArtifactExpansion";
import { sessionRef, splitSessionRef } from "./hostTransport";
import { FileRequestError, fileReason, fileSize, localFileSession, sessionFiles, sourceLabel, type DirectoryPage, type FileContent, type FileEntry, type RegisteredFile } from "./sessionFiles";
import type { SessionView } from "./types";
import "./sessionFiles.css";

type Directory = { page?: DirectoryPage; loading?: boolean; error?: string; stale?: boolean };
type Selection = { kind: "file"; file: FileEntry } | { kind: "saved"; file: RegisteredFile };
type Filter = "all" | "registered" | "copies";
const order = (a: FileEntry, b: FileEntry) => Number(b.type === "directory") - Number(a.type === "directory") || a.name.localeCompare(b.name, undefined, { sensitivity: "base" });
const shortDate = (ts: number) => new Date(ts).toLocaleDateString(undefined, { month: "short", day: "numeric" });
const mediaLabel = (type: string) => type === "text/markdown" ? "Markdown" : type === "text/html" ? "HTML" : type.startsWith("image/") ? "Image" : type.startsWith("text/") ? "Text" : "File";
export function SessionFilesView({ session }: { session: SessionView }) {
  const [revision, setRevision] = useState(0);
  // Host/root changes invalidate every cache, request, selection and object URL.
  return <Files key={JSON.stringify([session.key, session.host, session.cwd, session.worktreePath, revision])} session={session} refresh={() => setRevision(n => n + 1)} />;
}
function Files({ session, refresh }: { session: SessionView; refresh: () => void }) {
  const container = useRef<HTMLElement>(null);
  const [narrow, setNarrow] = useState(false);
  useEffect(() => {
    if (!container.current || typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(entries => setNarrow(entries[0].contentRect.width <= 650));
    observer.observe(container.current); return () => observer.disconnect();
  }, []);
  const local = localFileSession(session);
  const savedKey = splitSessionRef(session.key).host !== "local" ? session.key : sessionRef(session.host || "local", session.key);
  const [directories, setDirectories] = useState<Record<string, Directory>>({});
  const dirs = useRef(directories); dirs.current = directories;
  const root = useRef("");
  const [rootPath, setRootPath] = useState("");
  const [unavailable, setUnavailable] = useState(local ? "" : "Remote — not browsable here");
  const [registry, setRegistry] = useState<RegisteredFile[] | null>(null);
  const [registryError, setRegistryError] = useState("");
  const [expanded, setExpanded] = useState(new Set<string>());
  const [filter, setFilter] = useState<Filter>("all");
  const [query, setQuery] = useState("");
  const [savedLimit, setSavedLimit] = useState(40);
  const [selection, setSelection] = useState<Selection | null>(null);
  const [reading, setReading] = useState(false);
  const alive = useRef(true);
  const requests = useRef(new Set<AbortController>());
  const pendingDirs = useRef(new Set<string>());
  const loadRef = useRef<(path: string) => void>(() => {});
  async function savedFallback() {
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
        if (path === ".") { setUnavailable(fileReason(result.reason)); setRegistry(result.artifacts?.filter(a => !a.removed_at) ?? null); setSelection(null); if (!result.artifacts) void savedFallback(); }
        setDirectories(d => ({ ...d, [path]: { error: fileReason(result.reason) } })); return;
      }
      if (root.current && result.root !== root.current) {
        setUnavailable("The session’s working directory changed. Refresh before browsing."); setSelection(null); setRegistry(null); setDirectories({}); return;
      }
      root.current = result.root; setRootPath(result.root);
      setRegistry(result.artifacts.filter(a => !a.removed_at)); setRegistryError("");
      if (cursor && result.next_cursor === cursor) throw new Error("The directory did not advance. Refresh before loading more.");
      const entries = more && previous ? [...new Map([...previous.entries, ...result.entries].map(e => [e.path, e])).values()].sort(order) : result.entries;
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
  const registered = (registry ?? []).filter(a => a.source_state === "present" && a.path && matches(`${a.path} ${a.title}`));
  const copies = (registry ?? []).filter(a => a.source_state !== "present" && matches(`${a.title} ${a.source_path}`));
  const canBrowse = local && !unavailable;
  function registryRow(a: RegisteredFile, currentFile: boolean) {
    return <button key={a.id} className="rd-file-row rd-file-snapshot" aria-pressed={selection?.kind === "saved" && selection.file.id === a.id} title={`${a.title} · ${a.kind ?? "other"}`}
      aria-label={`${mediaLabel(a.media_type)} ${a.title}`} onClick={() => {
        if (currentFile && a.path) choose({ kind: "file", file: { path: a.path, name: a.path.split("/").pop()!, type: "file", size: null, modified_at: 0, browsable: true, content_limit: 5242880, artifact: a } });
        else choose({ kind: "saved", file: a });
      }}><span><strong>{currentFile ? a.path : a.title}</strong><small>{currentFile ? `${a.title} · registered ${a.kind ?? "artifact"}` : `${sourceLabel(a.source_state)} · ${a.kind ?? "artifact"}`}</small></span><span className="rd-file-size">{fileSize(a.size)}<small>saved</small></span></button>;
  }
  return <section ref={container} className={`rd-session-files${narrow ? " narrow" : ""}`} aria-label="Session files">
    <header className="rd-files-heading"><div><h2>Files <span>Read-only</span></h2><p>{rootPath || (local ? session.worktreePath || session.cwd || "No working directory recorded" : "Remote workspace")}</p></div><button className="rd-btn rd-btn-sm" onClick={refresh}>Refresh</button></header>
    <div className="rd-files-toolbar"><div role="group" aria-label="Files filter">{([['all', 'All files'], ['registered', 'Registered'], ['copies', 'Saved copies']] as const).map(([id, title]) => <button key={id} aria-pressed={filter === id} disabled={id !== 'copies' && !canBrowse} onClick={() => { setFilter(id); setQuery(""); setSavedLimit(40); }}>{title}</button>)}</div><input aria-label={filter === "all" ? "Filter loaded files" : "Filter registered artifacts"} placeholder={filter === "all" ? "Filter loaded files…" : "Filter registered artifacts…"} value={query} onChange={e => setQuery(e.target.value)} /></div>
    {!canBrowse && filter !== "copies" ? <div className="rd-files-unavailable"><h3>{unavailable}</h3><p>{!local ? "These files live on another machine. No local files are shown in their place." : "No directory is shown. Saved artifact copies are separate from files on disk."}</p><button className="rd-btn" onClick={() => setFilter("copies")}>View saved copies</button></div> : <div className={`rd-files-layout${reading ? " reading" : ""}`}>
      <nav className="rd-files-tree" aria-label="Files and saved copies"><div className="rd-files-columns"><span>Name</span><span>Size</span><span>Modified</span></div><div className="rd-files-scroll">
        {filter === "all" ? query ? <>{loaded.filter(e => matches(e.path)).map(e => row(e, 0, true))}{!loaded.some(e => matches(e.path)) && <p className="rd-files-note">No matching loaded files. Unexpanded folders have not been searched.</p>}</> : directory(".") : <>
          {registryError && <p className="rd-files-note" role="alert">Saved artifact metadata unavailable: {registryError}</p>}
          {registry === null && !registryError && <p className="rd-files-note" role="status">Loading saved artifacts…</p>}
          {(filter === "registered" ? registered : copies).slice(0, savedLimit).map(a => registryRow(a, filter === "registered"))}
          {registry && !(filter === "registered" ? registered : copies).length && <p className="rd-files-note">No matching {filter === "registered" ? "registered files in this worktree" : "saved copies"}.</p>}
          {(filter === "registered" ? registered : copies).length > savedLimit && <button className="rd-files-more" onClick={() => setSavedLimit(n => n + 40)}>Show more saved artifacts</button>}
        </>}
      </div><p className="rd-files-footnote">{filter === "all" ? "Ignore rules applied · expand folders to load them · filter searches loaded paths only" : filter === "registered" ? "Selected session’s registrations · saved sizes may differ from current files" : "Saved snapshots · outside-root paths are not checked for existence"}</p></nav>
      {selection ? <FileReader key={selection.kind + ":" + (selection.kind === "file" ? selection.file.path : selection.file.id)} session={session} savedKey={savedKey} selection={selection} back={() => setReading(false)} saved={a => choose({ kind: "saved", file: a })} /> : <section className="rd-file-reader"><p className="rd-files-note">Choose a file or saved artifact to read it.</p></section>}
    </div>}
  </section>;
}
function FileReader({ session, savedKey, selection, back: backToList, saved }: { session: SessionView; savedKey: string; selection: Selection; back: () => void; saved: (a: RegisteredFile) => void }) {
  const fileSession = useMemo(() => ({ key: session.key, host: session.host, cwd: session.cwd, worktreePath: session.worktreePath }), [session.key, session.host, session.cwd, session.worktreePath]);
  const [content, setContent] = useState<FileContent | ArtifactContent | null>(null);
  const [error, setError] = useState("");
  const [url, setUrl] = useState("");
  const [retry, setRetry] = useState(0);
  const [feedback, setFeedback] = useState<FeedbackTarget | null>(null);
  const [feedbackStatus, setFeedbackStatus] = useState("");
  const feedbackButton = useRef<HTMLButtonElement>(null);
  const readerLive = useRef(true);
  useEffect(() => { readerLive.current = true; return () => { readerLive.current = false; }; }, []);
  function closeFeedback() {
    setFeedback(null); feedbackButton.current?.focus();
    if (selection.kind === "saved") void api.artifact(savedKey, selection.file.id).then(result => {
      if (readerLive.current) setContent(previous => previous?.sha256 === result.artifact.sha256 ? previous : result.artifact);
    }).catch(() => { /* Keep the last readable snapshot if refresh is unavailable. */ });
  }
  const stored = selection.kind === "saved";
  const file = selection.file;
  const name = stored ? selection.file.source_path.split("/").pop() || selection.file.title : selection.file.name;
  const title = stored ? selection.file.title : name;
  const { expanded, viewer, back, expand, close } = useArtifactExpansion(`${savedKey}:${selection.kind}:${stored ? selection.file.id : selection.file.path}`);
  useEffect(() => {
    let live = true; const controller = new AbortController(); setContent(null); setError("");
    async function read() {
      try {
        if (selection.kind === "saved") {
          const result = await api.artifact(savedKey, selection.file.id);
          if (live) setContent(result.artifact);
        } else {
          if (selection.file.type !== "file") { setError(selection.file.type === "symlink" ? "Symbolic link — not followed. DuckTerm does not browse or download its target." : "Only regular files can be previewed or downloaded."); return; }
          if (!selection.file.browsable) { setError("This file exceeds the 5 MiB preview and download limit."); return; }
          const result = await sessionFiles.content(fileSession, selection.file.path, controller.signal);
          if (live) { if (result.available) setContent(result); else setError(fileReason(result.reason)); }
        }
      } catch (cause) { if (live && !controller.signal.aborted) setError((cause as Error).message); }
    }
    void read(); return () => { live = false; controller.abort(); };
  }, [selection, fileSession, savedKey, retry]);
  useEffect(() => {
    if (!content) { setUrl(""); return; }
    const bytes = Uint8Array.from(atob(content.content_base64), c => c.charCodeAt(0));
    const next = URL.createObjectURL(new Blob([bytes], { type: "application/octet-stream" }));
    setUrl(next); return () => URL.revokeObjectURL(next);
  }, [content]);
  useEffect(() => {
    if (!expanded) return;
    const escape = (e: KeyboardEvent) => { if (e.key === "Escape" && !feedback && !e.defaultPrevented) { e.preventDefault(); close(); } };
    window.addEventListener("keydown", escape); return () => window.removeEventListener("keydown", escape);
  }, [expanded, feedback, close]);
  const savedContent = stored && content && "id" in content ? content : null;
  const artifact = selection.kind === "saved" ? selection.file : selection.file.artifact;
  return <section ref={viewer} className={`rd-file-reader rd-artifact-viewer${expanded ? " rd-artifact-expanded" : ""}`} role={expanded ? "dialog" : undefined} aria-modal={expanded || undefined} aria-label={expanded ? title : "File preview"}>
    <header className="rd-file-reader-head rd-artifact-detail">
      {expanded ? <button ref={back} className="rd-btn rd-btn-sm" onClick={close}>← Back to sessions</button> : <button className="rd-files-back" onClick={backToList}>← Back to files</button>}
      <div className="rd-artifact-title"><h3><button className="rd-artifact-title-button" aria-label={`Expand ${title}`} disabled={expanded} onClick={e => expand(e.currentTarget)}>{title}</button></h3><p className="rd-file-path">{stored ? selection.file.source_path : selection.file.path}</p><small>{content ? fileSize(content.size) : fileSize(file.size)} · {stored ? "Saved" : "Modified"} {content && "modified_at" in content ? shortDate(content.modified_at) : stored ? shortDate(content && "updated_at" in content ? content.updated_at : selection.file.updated_at) : selection.file.modified_at ? shortDate(selection.file.modified_at) : "—"}</small></div>
      <div className="rd-artifact-actions"><button className="rd-btn rd-btn-sm" hidden={expanded} onClick={e => expand(e.currentTarget)}>Expand</button>{stored && <button ref={feedbackButton} className="rd-btn rd-btn-sm" disabled={!savedContent} onClick={e => { if (savedContent) { const rect = e.currentTarget.getBoundingClientRect(); setFeedback({ artifact: savedContent, quote: "", left: rect.left, bottom: rect.bottom }); setFeedbackStatus(""); } }}>Feedback</button>}{url && content && <a className="rd-btn rd-btn-sm" href={url} download={name}>Download</a>}</div>
    </header>
    <div className="rd-file-source"><span>{stored ? "Saved artifact snapshot" : "Current file"}</span>{artifact && <><span>{artifact.kind ?? "artifact"}</span>{!stored && <button className="rd-file-saved-link" onClick={() => saved(artifact)}>View saved version</button>}</>}</div>
    {stored && <p className="rd-file-source-note">{selection.file.source_state === "missing" ? "Content-only: the original file is missing. This is the stored artifact snapshot." : selection.file.source_state === "outside_root" ? "The original path is outside this worktree; its presence is not checked here." : selection.file.source_state === "unavailable" ? "Original file availability is unknown. You are reading the stored artifact snapshot." : "Saved artifact snapshot. The current file may have changed since registration."}</p>}
    {feedbackStatus && <p className="rd-files-note" role="status">{feedbackStatus}</p>}
    {error ? <div className="rd-files-note" role="alert"><p>{error}</p><button className="rd-btn rd-btn-sm" onClick={() => setRetry(n => n + 1)}>Retry preview</button></div> : content ? <ArtifactPreview key={content.sha256} artifact={{ ...content, title }} onSelect={quote => { if (savedContent && !feedback) { setFeedback({ artifact: savedContent, ...quote }); setFeedbackStatus(""); } }} onEscape={() => { if (feedback) window.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape" })); else if (expanded) close(); }} /> : <p className="rd-files-note" role="status">Loading preview…</p>}
    {feedback && <ArtifactFeedback sessionKey={savedKey} sessionName={session.label} target={feedback} onClose={closeFeedback} onSent={() => { setFeedback(null); setFeedbackStatus("Sent to the agent"); feedbackButton.current?.focus(); }} />}
  </section>;
}
