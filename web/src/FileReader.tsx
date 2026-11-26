import { useEffect, useMemo, useRef, useState } from "react";
import { api, type Artifact, type ArtifactContent, type ArtifactKind } from "./api";
import { ArtifactPreview } from "./ArtifactsView";
import { ArtifactFeedback, type ArtifactSelection, type FeedbackTarget } from "./ArtifactFeedback";
import { ARTIFACT_KINDS } from "./artifactKinds";
import { FileCommentHistory } from "./FileCommentHistory";
import { fileComments, type CommentIdentity, type StoredComment } from "./fileComments";
import { useArtifactExpansion } from "./useArtifactExpansion";
import { fileReason, fileSize, sessionFiles, type FileContent, type RegisteredFile } from "./sessionFiles";
import type { SessionView } from "./types";
import type { FileSelection } from "./SessionFilesView";

export function FileReader({ session, savedKey, selection, back: backToList, saved, onOpenArtifact, onMetadata }: {
  session: SessionView; savedKey: string; selection: FileSelection; back: () => void; saved: (a: RegisteredFile) => void;
  onOpenArtifact?: (id: string) => void; onMetadata: (a: Artifact) => void;
}) {
  const fileSession = useMemo(() => ({ key: session.key, host: session.host, cwd: session.cwd, worktreePath: session.worktreePath }), [session.key, session.host, session.cwd, session.worktreePath]);
  const [content, setContent] = useState<FileContent | ArtifactContent | null>(null);
  const [error, setError] = useState("");
  const [url, setUrl] = useState("");
  const [retry, setRetry] = useState(0);
  const [feedback, setFeedback] = useState<FeedbackTarget | null>(null);
  const [feedbackStatus, setFeedbackStatus] = useState("");
  const [commentsOpen, setCommentsOpen] = useState(false);
  const [comments, setComments] = useState<StoredComment[]>([]);
  const [commentsError, setCommentsError] = useState("");
  const [commentsLoading, setCommentsLoading] = useState(true);
  const [commentRevision, setCommentRevision] = useState(0);
  const [source, setSource] = useState(false);
  const [locate, setLocate] = useState<{ quote: string; request: number }>();
  const [locateStatus, setLocateStatus] = useState("");
  const [metadataBusy, setMetadataBusy] = useState(false);
  const [metadataError, setMetadataError] = useState("");
  const [metadata, setMetadata] = useState<Artifact | null>(null);
  const feedbackButton = useRef<HTMLButtonElement>(null);
  const liveReader = useRef(true);
  useEffect(() => { liveReader.current = true; return () => { liveReader.current = false; }; }, []);
  function closeFeedback() {
    setFeedback(null); feedbackButton.current?.focus();
    if (selection.kind === "saved") void api.artifact(savedKey, selection.file.id).then(result => {
      if (liveReader.current) setContent(previous => previous?.sha256 === result.artifact.sha256 ? previous : result.artifact);
    }).catch(() => { /* Retain readable content if refresh is unavailable. */ });
  }
  const stored = selection.kind === "saved";
  const initialArtifact = stored ? selection.file : selection.file.artifact;
  const artifact = metadata ?? initialArtifact;
  const name = stored ? selection.file.source_path.split("/").pop() || selection.file.title : selection.file.name;
  const title = stored ? selection.file.title : name;
  const identityKey = `${savedKey}:${selection.kind}:${stored ? selection.file.id : selection.file.path}`;
  const { expanded, viewer, back, expand, close } = useArtifactExpansion(identityKey);
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
    let live = true;
    setCommentsLoading(true); setCommentsError("");
    void api.annotations(savedKey).then(result => { if (live) setComments(result.annotations); }).catch(cause => { if (live) setCommentsError((cause as Error).message); }).finally(() => { if (live) setCommentsLoading(false); });
    return () => { live = false; };
  }, [savedKey, commentRevision]);
  useEffect(() => {
    if (!expanded) return;
    const escape = (e: KeyboardEvent) => { if (e.key === "Escape" && !feedback && !e.defaultPrevented) { e.preventDefault(); close(); } };
    window.addEventListener("keydown", escape); return () => window.removeEventListener("keydown", escape);
  }, [expanded, feedback, close]);
  const savedContent = stored && content && "id" in content ? content : null;
  const currentContent = !stored && content && "root" in content ? content : null;
  const identity: CommentIdentity | null = savedContent ? { artifactId: savedContent.id, sha256: savedContent.sha256 } : currentContent ? { root: currentContent.root, path: currentContent.path, sha256: currentContent.sha256 } : null;
  const history = identity ? fileComments(comments, identity) : [];
  const rich = content?.media_type === "text/html" || content?.media_type === "text/markdown";
  function select(quote: ArtifactSelection) {
    if (feedback) return;
    if (savedContent) setFeedback({ artifact: savedContent, ...quote });
    else if (currentContent) setFeedback({ file: currentContent, ...quote });
    setFeedbackStatus("");
  }
  async function changeLabel(kind: ArtifactKind) {
    if (!artifact || metadataBusy) return;
    setMetadataBusy(true); setMetadataError("");
    try { const result = await api.updateArtifact(savedKey, artifact.id, { kind }); setMetadata(result.artifact); onMetadata(result.artifact); }
    catch (cause) { setMetadataError((cause as Error).message); }
    finally { setMetadataBusy(false); }
  }
  return <section ref={viewer} className={`rd-file-reader rd-artifact-viewer${expanded ? " rd-artifact-expanded" : ""}`} role={expanded ? "dialog" : undefined} aria-modal={expanded || undefined} aria-label={expanded ? title : "File preview"}>
    <header className="rd-file-reader-head rd-artifact-detail">
      {expanded ? <button ref={back} className="rd-btn rd-btn-sm" onClick={close}>← Back to sessions</button> : <button className="rd-files-back" onClick={backToList}>← Back to list</button>}
      <div className="rd-artifact-title"><h3><button className="rd-artifact-title-button" aria-label={`Expand ${title}`} disabled={expanded} onClick={e => expand(e.currentTarget)}>{title}</button></h3><p className="rd-file-path" title={stored ? selection.file.source_path : selection.file.path}>{stored ? selection.file.source_path : selection.file.path}</p><small>{fileSize(content?.size ?? selection.file.size)} · {stored ? "Saved artifact" : "Current file"}</small></div>
      <div className="rd-artifact-actions">
        <button ref={feedbackButton} className="rd-btn rd-btn-sm" disabled={!identity} onClick={e => { const rect = e.currentTarget.getBoundingClientRect(); select({ quote: "", left: rect.left, bottom: rect.bottom }); }}>Leave feedback</button>
        <button className="rd-btn rd-btn-sm" aria-expanded={commentsOpen} onClick={() => setCommentsOpen(v => !v)}>Comments ({history.length})</button>
        <button className="rd-btn rd-btn-sm" hidden={expanded} onClick={e => expand(e.currentTarget)}>Expand</button>
        {url && content && <a className="rd-btn rd-btn-sm" href={url} download={name}>Download</a>}
      </div>
    </header>
    <div className="rd-file-source">
      {rich ? <div role="group" aria-label="Reader mode"><button aria-pressed={!source} onClick={() => { setSource(false); setLocateStatus(""); }}>{content?.media_type === "text/markdown" ? "Rendered" : "Browser"}</button><button aria-pressed={source} onClick={() => { setSource(true); setLocateStatus(""); }}>Source</button></div> : <span>Source</span>}
      {artifact && <><select aria-label="Change artifact label" value={artifact.kind ?? "other"} disabled={metadataBusy} onChange={e => void changeLabel(e.target.value as ArtifactKind)}>{Object.entries(ARTIFACT_KINDS).map(([kind, label]) => <option key={kind} value={kind}>{label}</option>)}</select>
        {!stored && <button className="rd-file-saved-link" onClick={() => saved({ ...initialArtifact!, ...artifact })}>View saved version</button>}
        {onOpenArtifact && <button className="rd-file-saved-link" onClick={() => onOpenArtifact(artifact.id)}>Open in Artifacts</button>}</>}
    </div>
    {stored && <p className="rd-file-source-note">{selection.file.source_state === "missing" ? "Content-only: the original file is missing. This is the stored artifact snapshot." : selection.file.source_state === "outside_root" ? "The original path is outside this worktree; its presence is not checked here." : selection.file.source_state === "unavailable" ? "Original file availability is unknown. You are reading the stored artifact snapshot." : "Saved artifact snapshot. The current file may have changed since registration."}</p>}
    {content?.media_type === "text/html" && !source && <p className="rd-file-source-note">Interactive preview · bundled scripts run in isolation. External resources and network requests are blocked.</p>}
    {metadataError && <p className="rd-files-note" role="alert">{metadataError}</p>}
    {feedbackStatus && <p className="rd-files-note" role="status">{feedbackStatus}</p>}
    {locateStatus && <p className="rd-files-note" role="status">{locateStatus}</p>}
    <div className={`rd-file-reading-area${commentsOpen ? " with-comments" : ""}`}>
      <div className="rd-file-document">{error ? <div className="rd-files-note" role="alert"><p>{error}</p><button className="rd-btn rd-btn-sm" onClick={() => setRetry(n => n + 1)}>Retry preview</button></div> : content ? <ArtifactPreview key={`${content.sha256}:${source}`} artifact={{ ...content, title }} source={source} interactive locate={locate} onLocate={setLocateStatus} onSelect={select} onEscape={() => { if (feedback) window.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape" })); else if (expanded) close(); }} /> : <p className="rd-files-note" role="status">Loading preview…</p>}</div>
      {commentsOpen && <FileCommentHistory comments={history} error={commentsError} loading={commentsLoading} onClose={() => setCommentsOpen(false)} retry={() => setCommentRevision(n => n + 1)} onLocate={quote => { setLocate({ quote, request: Date.now() }); setLocateStatus(""); }} />}
    </div>
    {feedback && <ArtifactFeedback sessionKey={savedKey} sessionName={session.label} target={feedback} onClose={closeFeedback} onSent={() => { setFeedback(null); setFeedbackStatus("Sent to the agent"); setCommentRevision(n => n + 1); setCommentsOpen(true); feedbackButton.current?.focus(); }} />}
  </section>;
}
