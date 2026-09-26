import { useEffect, useMemo, useState } from "react";
import DOMPurify from "dompurify";
import { api, Artifact, ArtifactContent } from "./api";
import { html } from "./render";
import "./artifacts.css";

const POLICY = `<meta http-equiv="Content-Security-Policy" content="default-src 'none'; img-src data: blob:; style-src 'unsafe-inline'; font-src data:; form-action 'none'; base-uri 'none'">`;
const PAPER = `<style>html{color-scheme:light}body{margin:0;padding:32px 38px;background:#fafbf8;color:#27352c;font:15px/1.65 -apple-system,BlinkMacSystemFont,sans-serif;overflow-wrap:anywhere}h1{font-size:27px;line-height:1.25}h2{font-size:19px;margin-top:28px}pre{white-space:pre-wrap;background:#edf0e9;padding:14px;border-radius:6px}code{font-size:13px}img{max-width:100%}table{border-collapse:collapse;width:100%}td,th{border:1px solid #d8dfd5;padding:8px}blockquote{border-left:3px solid #a8b9aa;margin-left:0;padding-left:16px;color:#536758}</style>`;

export function previewDocument(source: string, markdown: boolean): string {
  const clean = DOMPurify.sanitize(markdown ? html(source) : source, {
    WHOLE_DOCUMENT: true,
    FORBID_TAGS: ["script", "iframe", "object", "embed", "meta", "base", "link", "form"],
    FORBID_ATTR: ["href", "action", "formaction", "srcdoc", "target"],
  });
  // First in the document, before any user styles/images. The iframe has no
  // sandbox exceptions, so it cannot execute scripts or access the app origin.
  return `<!doctype html>${POLICY}${markdown ? PAPER : ""}${clean}`;
}

function label(artifact: Artifact): string {
  if (artifact.media_type === "text/markdown") return "Markdown";
  if (artifact.media_type === "text/html") return "HTML";
  if (artifact.media_type.startsWith("image/")) return "Image";
  if (artifact.media_type === "application/pdf") return "PDF";
  return artifact.media_type.startsWith("text/") ? "Text" : "File";
}

function size(bytes: number): string {
  return bytes < 1024 ? `${bytes} B` : bytes < 1024 * 1024 ? `${(bytes / 1024).toFixed(1)} KB` : `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function ArtifactPreview({ artifact }: { artifact: ArtifactContent }) {
  const [imageFailed, setImageFailed] = useState(false);
  const document = useMemo(() => {
    if (!artifact.media_type.startsWith("text/")) return null;
    const bytes = Uint8Array.from(atob(artifact.content_base64), (character) => character.charCodeAt(0));
    const text = new TextDecoder().decode(bytes);
    if (artifact.media_type === "text/plain") return text;
    return previewDocument(text, artifact.media_type === "text/markdown");
  }, [artifact]);
  if (imageFailed) return <div className="rd-artifact-empty"><h3>Image preview unavailable</h3><p>Download the saved file to open it in another app.</p></div>;
  if (artifact.media_type.startsWith("image/")) {
    return <div className="rd-artifact-image"><img src={`data:${artifact.media_type};base64,${artifact.content_base64}`} alt={artifact.title} onError={() => setImageFailed(true)} /></div>;
  }
  if (artifact.media_type === "text/plain") return <pre className="rd-artifact-text">{document}</pre>;
  if (document !== null) return <iframe className="rd-artifact-frame" title={`Preview of ${artifact.title}`} sandbox="" referrerPolicy="no-referrer" srcDoc={document} />;
  return <div className="rd-artifact-empty"><h3>Download to view this file</h3><p>A preview is not available for this format.</p></div>;
}

export function ArtifactsView({ sessionKey, sessionName }: { sessionKey: string; sessionName: string }) {
  const [files, setFiles] = useState<Artifact[] | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [content, setContent] = useState<ArtifactContent | null>(null);
  const [error, setError] = useState("");
  const [previewError, setPreviewError] = useState("");
  const [retry, setRetry] = useState(0);
  const [removing, setRemoving] = useState(false);
  const [downloadUrl, setDownloadUrl] = useState("");
  const selected = files?.find((file) => file.id === selectedId) ?? files?.[0];
  const visibleContent = content?.id === selected?.id && content?.sha256 === selected?.sha256 && content?.updated_at === selected?.updated_at ? content : null;

  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout>;
    async function refresh() {
      try {
        const result = await api.artifacts(sessionKey);
        if (!cancelled) { setFiles(result.artifacts); setError(""); }
      } catch (cause) {
        if (!cancelled) setError((cause as Error).message);
      } finally {
        if (!cancelled) timer = setTimeout(refresh, 3000);
      }
    }
    void refresh();
    return () => { cancelled = true; clearTimeout(timer); };
  }, [sessionKey, retry]);

  useEffect(() => {
    let cancelled = false;
    setContent(null);
    setPreviewError("");
    if (selected?.id) {
      void api.artifact(sessionKey, selected.id).then((result) => {
        if (!cancelled) setContent(result.artifact);
      }).catch((cause: Error) => { if (!cancelled) setPreviewError(cause.message); });
    }
    return () => { cancelled = true; };
  }, [sessionKey, selected?.id, selected?.sha256, selected?.updated_at, retry]);

  useEffect(() => {
    setDownloadUrl("");
    if (!visibleContent) return;
    const bytes = Uint8Array.from(atob(visibleContent.content_base64), (character) => character.charCodeAt(0));
    const url = URL.createObjectURL(new Blob([bytes], { type: visibleContent.media_type }));
    setDownloadUrl(url);
    return () => URL.revokeObjectURL(url);
  }, [visibleContent]);

  async function remove() {
    if (!selected || removing || !window.confirm(`Remove the saved copy of “${selected.title}”? The original file will stay intact.`)) return;
    setRemoving(true);
    try {
      await api.removeArtifact(sessionKey, selected.id);
      setFiles((current) => current?.filter((file) => file.id !== selected.id) ?? []);
    } catch (cause) { setError((cause as Error).message); }
    finally { setRemoving(false); }
  }

  return <section className="rd-artifacts" aria-label="Session artifacts">
    <header className="rd-artifacts-heading"><div><h2>Artifacts</h2><p>Saved outputs from {sessionName}</p></div><button className="rd-btn rd-btn-sm" onClick={() => setRetry((value) => value + 1)}>Refresh</button></header>
    {error && <p className="rd-artifact-error" role="alert">{error}</p>}
    {files === null && !error && <p className="rd-artifact-note" role="status">Loading artifacts…</p>}
    {files?.length === 0 && <div className="rd-artifact-empty"><h3>Your outputs will appear here</h3><p>When this agent registers a document, mockup, image, or exported file, it is saved here for you to preview and download.</p><p>Local to this Mac. Nothing is published or shared.</p></div>}
    {!!files?.length && <div className="rd-artifact-layout">
      <nav className="rd-artifact-list" aria-label="Saved artifacts">{files.map((file) => <button key={file.id} className={`rd-artifact-item${file.id === selected?.id ? " selected" : ""}`} aria-pressed={file.id === selected?.id} onClick={() => setSelectedId(file.id)}>
        <span className="rd-artifact-type">{label(file)}</span><strong>{file.title}</strong><small>{file.source_path.split("/").pop()} · {size(file.size)}</small><small>{new Date(file.updated_at).toLocaleString()}</small>
      </button>)}</nav>
      {selected && <div className="rd-artifact-viewer"><header className="rd-artifact-detail"><div><h3>{selected.title}</h3><p className="rd-artifact-path">{selected.source_path}</p></div><div className="rd-artifact-actions">
        {downloadUrl && visibleContent && <a className="rd-btn rd-btn-sm" href={downloadUrl} download={selected.source_path.split("/").pop() || "artifact"}>Download</a>}
        <button className="rd-btn rd-btn-sm" disabled={removing} onClick={() => void remove()}>{removing ? "Removing…" : "Remove"}</button>
      </div></header>
      {previewError ? <p role="alert">Could not load the saved copy: {previewError}</p> : visibleContent ? <ArtifactPreview key={`${visibleContent.id}:${visibleContent.sha256}`} artifact={visibleContent} /> : <p role="status">Loading preview…</p>}
      <p className="rd-artifact-note">{selected.media_type === "text/html" ? "Saved HTML preview · Scripts and external resources are disabled." : "Saved copy · Available even if the original file moves."}</p>
      </div>}
    </div>}
  </section>;
}
