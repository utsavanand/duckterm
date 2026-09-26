import { useEffect, useMemo, useRef, useState } from "react";
import DOMPurify from "dompurify";
import { api, Artifact, ArtifactContent } from "./api";
import { html } from "./render";
import "./artifacts.css";
import { ArtifactFeedback, ArtifactSelection, FeedbackTarget } from "./ArtifactFeedback";

const POLICY = `<meta http-equiv="Content-Security-Policy" content="default-src 'none'; img-src data: blob:; style-src 'unsafe-inline'; font-src data:; form-action 'none'; base-uri 'none'">`;
const PAPER = `<style>html{color-scheme:light}body{margin:0;padding:32px 38px;background:#fafbf8;color:#27352c;font:15px/1.65 -apple-system,BlinkMacSystemFont,sans-serif;overflow-wrap:anywhere}h1{font-size:27px;line-height:1.25}h2{font-size:19px;margin-top:28px}pre{white-space:pre-wrap;background:#edf0e9;padding:14px;border-radius:6px}code{font-size:13px}img{max-width:100%}table{border-collapse:collapse;width:100%}td,th{border:1px solid #d8dfd5;padding:8px}blockquote{border-left:3px solid #a8b9aa;margin-left:0;padding-left:16px;color:#536758}</style>`;

export function previewDocument(source: string, markdown: boolean, bridge?: { nonce: string; origin: string }): string {
  const clean = DOMPurify.sanitize(markdown ? html(source) : source, {
    WHOLE_DOCUMENT: true,
    FORBID_TAGS: ["script", "iframe", "object", "embed", "meta", "base", "link", "form"],
    FORBID_ATTR: ["href", "action", "formaction", "srcdoc", "target", "nonce"],
  });
  // Only our nonce-authorized selection reporter can execute. Artifact scripts
  // are removed; the frame retains an opaque origin (no allow-same-origin).
  const policy = bridge ? POLICY.replace("default-src 'none';", `default-src 'none'; script-src 'nonce-${bridge.nonce}';`) : POLICY;
  const reporter = bridge ? `<script nonce="${bridge.nonce}">(() => {
    const report = () => {
      const selection = window.getSelection(), quote = selection?.toString().trim();
      if (!quote || quote.length > 8000 || !selection.rangeCount) return;
      const rect = selection.getRangeAt(0).getBoundingClientRect();
      parent.postMessage({type: 'artifact-selection', channel: ${JSON.stringify(bridge.nonce)}, quote, left: rect.left, bottom: rect.bottom}, ${JSON.stringify(bridge.origin)});
    };
    document.addEventListener('mouseup', report);
    document.addEventListener('keyup', event => { if (event.key === 'Shift' || event.key.startsWith('Arrow')) report(); });
  })();</script>` : "";
  return `<!doctype html>${policy}${markdown ? PAPER : ""}${clean}${reporter}`;
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

function ArtifactPreview({ artifact, onSelect }: { artifact: ArtifactContent; onSelect: (selection: ArtifactSelection) => void }) {
  const frame = useRef<HTMLIFrameElement>(null);
  const [nonce] = useState(() => Array.from(crypto.getRandomValues(new Uint8Array(24)), b => b.toString(16).padStart(2, "0")).join(""));
  useEffect(() => {
    const receive = (event: MessageEvent) => {
      const data = event.data;
      if (event.source !== frame.current?.contentWindow || event.origin !== "null" || !data || data.type !== "artifact-selection" || data.channel !== nonce) return;
      if (typeof data.quote !== "string" || !data.quote.trim() || data.quote.length > 8000 || !Number.isFinite(data.left) || !Number.isFinite(data.bottom)) return;
      const box = frame.current.getBoundingClientRect();
      onSelect({ quote: data.quote.trim(), left: box.left + Math.max(0, Math.min(data.left, box.width)), bottom: box.top + Math.max(0, Math.min(data.bottom, box.height)) });
    };
    window.addEventListener("message", receive);
    return () => window.removeEventListener("message", receive);
  }, [nonce, onSelect]);
  function selectText(event: React.MouseEvent<HTMLElement> | React.KeyboardEvent<HTMLElement>) {
    const selection = window.getSelection();
    if (!selection?.rangeCount || !selection.toString().trim() || selection.toString().length > 8000) return;
    const range = selection.getRangeAt(0);
    if (!event.currentTarget.contains(range.commonAncestorContainer)) return;
    const box = range.getBoundingClientRect();
    onSelect({ quote: selection.toString().trim(), left: box.left, bottom: box.bottom });
  }
  const [imageFailed, setImageFailed] = useState(false);
  const document = useMemo(() => {
    if (!artifact.media_type.startsWith("text/")) return null;
    const bytes = Uint8Array.from(atob(artifact.content_base64), (character) => character.charCodeAt(0));
    const text = new TextDecoder().decode(bytes);
    if (artifact.media_type === "text/plain") return text;
    return previewDocument(text, artifact.media_type === "text/markdown", { nonce, origin: window.location.origin });
  }, [artifact, nonce]);
  if (imageFailed) return <div className="rd-artifact-empty"><h3>Image preview unavailable</h3><p>Download the saved file to open it in another app.</p></div>;
  if (artifact.media_type.startsWith("image/")) {
    return <div className="rd-artifact-image"><img src={`data:${artifact.media_type};base64,${artifact.content_base64}`} alt={artifact.title} onError={() => setImageFailed(true)} /></div>;
  }
  if (artifact.media_type === "text/plain") return <pre className="rd-artifact-text" tabIndex={0} onMouseUp={selectText} onKeyUp={selectText}>{document}</pre>;
  if (document !== null) return <iframe ref={frame} className="rd-artifact-frame" title={`Preview of ${artifact.title}`} sandbox="allow-scripts" referrerPolicy="no-referrer" srcDoc={document} />;
  return <div className="rd-artifact-empty"><h3>Download to view this file</h3><p>A preview is not available for this format.</p></div>;
}

export function ArtifactsView({ sessionKey, sessionName }: { sessionKey: string; sessionName: string }) {
  const [feedback, setFeedback] = useState<FeedbackTarget | null>(null);
  const [feedbackStatus, setFeedbackStatus] = useState("");
  const feedbackButton = useRef<HTMLButtonElement>(null);
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
      setFeedback(null);
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
      <nav className="rd-artifact-list" aria-label="Saved artifacts">{files.map((file) => <button key={file.id} className={`rd-artifact-item${file.id === selected?.id ? " selected" : ""}`} aria-pressed={file.id === selected?.id} onClick={() => { setSelectedId(file.id); setFeedback(null); setFeedbackStatus(""); }}>
        <span className="rd-artifact-type">{label(file)}</span><strong>{file.title}</strong><small>{file.source_path.split("/").pop()} · {size(file.size)}</small><small>{new Date(file.updated_at).toLocaleString()}</small>
      </button>)}</nav>
      {selected && <div className="rd-artifact-viewer"><header className="rd-artifact-detail"><div><h3>{selected.title}</h3><p className="rd-artifact-path">{selected.source_path}</p></div><div className="rd-artifact-actions">
        <button ref={feedbackButton} className="rd-btn rd-btn-sm" disabled={!visibleContent} onClick={event => {
          if (!visibleContent) return;
          const box = event.currentTarget.getBoundingClientRect();
          setFeedback({ artifact: visibleContent, quote: "", left: box.left, bottom: box.bottom }); setFeedbackStatus("");
        }}>Feedback</button>
        {downloadUrl && visibleContent && <a className="rd-btn rd-btn-sm" href={downloadUrl} download={selected.source_path.split("/").pop() || "artifact"}>Download</a>}
        <button className="rd-btn rd-btn-sm" disabled={removing} onClick={() => void remove()}>{removing ? "Removing…" : "Remove"}</button>
      </div></header>
      {visibleContent?.media_type.startsWith("text/") && <p className="rd-artifact-note">Highlight text to send feedback to {sessionName}.</p>}
      {feedbackStatus && <p className="rd-artifact-note" role="status">{feedbackStatus}</p>}
      {previewError ? <p role="alert">Could not load the saved copy: {previewError}</p> : visibleContent ? <ArtifactPreview key={`${visibleContent.id}:${visibleContent.sha256}`} artifact={visibleContent} onSelect={selection => { if (!feedback) { setFeedback({ artifact: visibleContent, ...selection }); setFeedbackStatus(""); } }} /> : <p role="status">Loading preview…</p>}
      <p className="rd-artifact-note">{selected.media_type === "text/html" ? "Saved HTML preview · Scripts and external resources are disabled." : "Saved copy · Available even if the original file moves."}</p>
      </div>}
    </div>}
    {feedback && <ArtifactFeedback key={`${feedback.artifact.id}:${feedback.artifact.sha256}`} sessionKey={sessionKey} sessionName={sessionName} target={feedback} onClose={() => { setFeedback(null); feedbackButton.current?.focus(); }} onSent={() => { setFeedback(null); setFeedbackStatus("Sent to the agent"); feedbackButton.current?.focus(); }} />}
  </section>;
}
