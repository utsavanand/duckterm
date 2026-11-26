import { useEffect, useRef, useState } from "react";
import { version, getDocument, GlobalWorkerOptions, TextLayer, type PDFDocumentProxy, type RenderTask } from "pdfjs-dist";
import workerUrl from "pdfjs-dist/build/pdf.worker.min.mjs?url";
import type { ArtifactSelection } from "./ArtifactFeedback";
import { locateFileQuote } from "./locateFileQuote";
import "./pdfArtifact.css";

GlobalWorkerOptions.workerSrc = workerUrl;
const resources = new URL(import.meta.env.DEV ? "/node_modules/pdfjs-dist/" : `${import.meta.env.BASE_URL}assets/pdfjs-${version}/`, window.location.href).href;
export default function PdfArtifactPreview({ data, title, onSelect, locate, onLocate }: {
  data: string; title: string; onSelect: (selection: ArtifactSelection) => void;
  locate?: { quote: string; request: number; page?: number }; onLocate?: (status: string) => void;
}) {
  const [doc, setDoc] = useState<PDFDocumentProxy | null>(null);
  const [error, setError] = useState("");
  const [page, setPage] = useState(1);
  const [pageInput, setPageInput] = useState("1");
  const [mode, setMode] = useState<"pdf" | "text">("pdf");
  const [zoom, setZoom] = useState("fit");
  const [width, setWidth] = useState(600);
  const [text, setText] = useState("");
  const [ready, setReady] = useState(false);
  const [rendered, setRendered] = useState(0);
  const [retry, setRetry] = useState(0);
  const host = useRef<HTMLDivElement>(null);
  const paper = useRef<HTMLDivElement>(null);
  const plain = useRef<HTMLPreElement>(null);
  useEffect(() => {
    const element = host.current;
    if (!element) return;
    const observer = new ResizeObserver(() => setWidth(Math.max(160, element.clientWidth - 32)));
    observer.observe(element); setWidth(Math.max(160, element.clientWidth - 32));
    return () => observer.disconnect();
  }, []);
  useEffect(() => {
    let live = true;
    setDoc(null); setError(""); setReady(false);
    const task = getDocument({ data: Uint8Array.from(atob(data), c => c.charCodeAt(0)), enableXfa: false,
      cMapUrl: resources + "cmaps/", cMapPacked: true, standardFontDataUrl: resources + "standard_fonts/", wasmUrl: resources + "wasm/",
      maxImageSize: 16_777_216, canvasMaxAreaInBytes: 67_108_864 });
    task.onPassword = () => { if (live) setError("This PDF is password-protected. Download it to open it, or register an unlocked copy."); void task.destroy(); };
    void task.promise.then(pdf => { if (live) setDoc(pdf); }).catch(() => { if (live) setError(previous => previous || "This PDF could not be read. The saved file and its comments remain available."); });
    return () => { live = false; void task.destroy(); };
  }, [data, retry]);
  useEffect(() => {
    if (!doc) return;
    let live = true, renderTask: RenderTask | undefined, layer: TextLayer | undefined;
    setReady(false); setError(""); setText("");
    const container = paper.current!;
    container.replaceChildren();
    async function render() {
      const pdfPage = await doc!.getPage(page);
      if (!live) return;
      const original = pdfPage.getViewport({ scale: 1 });
      const scale = zoom === "fit" ? width / original.width : Number(zoom) / 100 * 96 / 72;
      const boundedScale = Math.min(scale, Math.sqrt(16_777_216 / (original.width * original.height)));
      if (!Number.isFinite(boundedScale) || boundedScale <= 0) throw new Error("Invalid page size");
      const viewport = pdfPage.getViewport({ scale: boundedScale });
      const content = await pdfPage.getTextContent();
      if (!live) return;
      const words = content.items.flatMap(item => "str" in item ? [item.str + (item.hasEOL ? "\n" : " ")] : []);
      setText(words.join(""));
      if (mode === "pdf") {
        const canvas = document.createElement("canvas"); canvas.setAttribute("aria-hidden", "true");
        const ratio = Math.min(window.devicePixelRatio || 1, 2, Math.sqrt(16_777_216 / (viewport.width * viewport.height)));
        canvas.width = Math.ceil(viewport.width * ratio); canvas.height = Math.ceil(viewport.height * ratio);
        canvas.style.width = `${viewport.width}px`; canvas.style.height = `${viewport.height}px`;
        const texts = document.createElement("div"); texts.className = "rd-pdf-text-layer";
        container.style.width = `${viewport.width}px`; container.style.height = `${viewport.height}px`;
        container.style.setProperty("--total-scale-factor", String(viewport.scale * pdfPage.userUnit));
        container.append(canvas, texts);
        renderTask = pdfPage.render({ canvas, viewport, transform: [ratio, 0, 0, ratio, 0, 0] });
        layer = new TextLayer({ textContentSource: content, container: texts, viewport });
        await Promise.all([renderTask.promise, layer.render()]);
      }
      if (live) { setReady(true); setRendered(n => n + 1); }
    }
    void render().catch(() => { if (live) setError("This page could not be displayed. Try another page or download the PDF. Your comments are kept."); });
    return () => { live = false; renderTask?.cancel(); layer?.cancel(); };
  }, [doc, page, width, zoom, mode, retry]);
  useEffect(() => {
    if (!locate || !doc) return;
    if (locate.page && locate.page > doc.numPages) { onLocate?.("That page is not available in this PDF."); return; }
    if (locate.page) { setPage(locate.page); setPageInput(String(locate.page)); }
  }, [locate, doc, onLocate]);
  useEffect(() => {
    if (!locate || !ready || (locate.page && locate.page !== page)) return;
    const element = mode === "text" ? plain.current : paper.current?.querySelector<HTMLElement>(".rd-pdf-text-layer");
    if (element && locate.quote) onLocate?.(locateFileQuote(element, locate.quote));
    else onLocate?.(`Page ${page} of ${doc?.numPages}.`);
  }, [locate, ready, rendered, page, mode, doc, onLocate]);
  function changePage(next: number) {
    if (!doc) return;
    const value = Math.min(doc.numPages, Math.max(1, Number.isFinite(next) ? Math.trunc(next) : page));
    setPage(value); setPageInput(String(value)); onLocate?.(""); host.current?.scrollTo(0, 0);
  }
  function select(event: React.MouseEvent | React.KeyboardEvent) {
    if (!ready) return;
    const selection = getSelection();
    if (!selection?.rangeCount || !selection.toString().trim()) return;
    const range = selection.getRangeAt(0);
    if (!event.currentTarget.contains(range.commonAncestorContainer)) return;
    const full = selection.toString().trim(), rect = range.getBoundingClientRect();
    onSelect({ quote: full.slice(0, 1000), page, pageSupplied: true, truncated: full.length > 1000, left: rect.left, bottom: rect.bottom });
  }
  return <section className="rd-pdf-viewer" aria-label={`PDF: ${title}`}>
    <div className="rd-pdf-toolbar">
      <div role="group" aria-label="PDF reading mode"><button aria-pressed={mode === "pdf"} onClick={() => setMode("pdf")}>PDF</button><button aria-pressed={mode === "text"} disabled={!ready || !text.trim()} onClick={() => setMode("text")}>Text</button></div>
      <button aria-label="Previous page" disabled={!doc || page <= 1} onClick={() => changePage(page - 1)}>‹</button>
      <label>Page <input aria-label="PDF page number" type="number" min="1" max={doc?.numPages} disabled={!doc} value={pageInput} onChange={e => setPageInput(e.target.value)} onBlur={() => changePage(Number(pageInput))} onKeyDown={e => { if (e.key === "Enter") changePage(Number(pageInput)); }} /> of {doc?.numPages ?? "—"}</label>
      <button aria-label="Next page" disabled={!doc || page >= doc.numPages} onClick={() => changePage(page + 1)}>›</button>
      <select aria-label="PDF zoom" value={zoom} disabled={mode === "text"} onChange={e => setZoom(e.target.value)}><option value="fit">Fit width</option><option value="100">100%</option><option value="150">150%</option></select>
      <button disabled={!ready || !!error} onClick={e => { const rect = e.currentTarget.getBoundingClientRect(); onSelect({ quote: "", page, left: rect.left, bottom: rect.bottom }); }}>Comment on page</button>
    </div>
    {error && <div className="rd-files-note" role="alert">{error} <button className="rd-btn rd-btn-sm" onClick={() => setRetry(n => n + 1)}>Retry PDF</button></div>}
    <div ref={host} className="rd-pdf-scroll" onMouseUp={select} onKeyUp={select}>
      {!ready && !error && <p className="rd-files-note" role="status">Loading PDF page…</p>}
      <div ref={paper} className="rd-pdf-paper" hidden={mode !== "pdf" || !!error} />
      {mode === "text" && !error && <pre ref={plain} className="rd-pdf-plain" tabIndex={0}>{text}</pre>}
    </div>
    <p className="rd-pdf-status" role="status">{ready && !error ? `Page ${page} of ${doc?.numPages} · ${text.trim() ? "Select text to leave feedback" : "No selectable text on this page. Use Comment on page."}` : error ? "Preview unavailable · Comments are preserved" : "Loading PDF…"}</p>
  </section>;
}
