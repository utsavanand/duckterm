import { useLayoutEffect, useMemo, useRef, useState } from "react";
import { Annotation, clearHighlights, highlightAnnotations } from "./annotationHighlights";
import { html } from "./render";

export function messageMarkup(source: string, plain = false) {
  return plain ? source.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;") : html(source);
}

export function AnnotatedText({ source, plain = false, annotations, className }: {
  source: string; plain?: boolean; annotations: Annotation[]; className: string;
}) {
  const root = useRef<HTMLDivElement>(null);
  const markup = useMemo(() => messageMarkup(source, plain), [source, plain]);
  const [popup, setPopup] = useState<{ ids: string[]; left: number; top?: number; bottom?: number } | null>(null);
  useLayoutEffect(() => {
    const el = root.current;
    if (!el) return;
    highlightAnnotations(el, annotations);
    return () => clearHighlights(el);
  }, [markup, annotations]);
  function reveal(target: EventTarget | null) {
    const mark = target instanceof Element ? target.closest<HTMLElement>("mark[data-annotation-ids]") : null;
    if (!mark) { setPopup(null); return; }
    const rect = mark.getBoundingClientRect();
    setPopup({ ids: JSON.parse(mark.dataset.annotationIds!), left: Math.max(12, Math.min(rect.left, window.innerWidth - 332)),
      ...(rect.bottom < window.innerHeight / 2 ? { top: rect.bottom + 6 } : { bottom: window.innerHeight - rect.top + 6 }) });
  }
  return <>
    <div ref={root} className={className} dangerouslySetInnerHTML={{ __html: markup }}
      onMouseOver={event => reveal(event.target)} onMouseLeave={() => setPopup(null)}
      onFocusCapture={event => reveal(event.target)} onBlurCapture={() => setPopup(null)}
      onKeyDown={event => { if (event.key === "Escape") setPopup(null); }} />
    {popup && <aside className="rd-annotation-tooltip" role="tooltip" style={{ left: popup.left, top: popup.top, bottom: popup.bottom }}>
      {annotations.filter(note => popup.ids.includes(note.id)).map(note => <p key={note.id}>{note.note}</p>)}
    </aside>}
  </>;
}
