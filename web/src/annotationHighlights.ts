export interface Annotation { id: string; quote: string; note: string; created_at: number }

// Browser selections may fold whitespace differently from the stored Markdown.
// Keep offsets back to the original text so we never rewrite markup as strings.
function normalized(text: string) {
  let value = "";
  const starts: number[] = [], ends: number[] = [];
  for (let i = 0; i < text.length; i++) {
    const char = /\s/.test(text[i]) ? " " : text[i];
    if (char === " " && value.endsWith(" ")) { ends[ends.length - 1] = i + 1; continue; }
    value += char; starts.push(i); ends.push(i + 1);
  }
  return { value, starts, ends };
}

function textNodes(root: HTMLElement) {
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  const nodes: { node: Text; start: number; end: number }[] = [];
  let text = "";
  while (walker.nextNode()) {
    const node = walker.currentNode as Text;
    if (node.parentElement?.closest("script, style, svg, .rd-mermaid, .rd-mermaid-svg")) continue;
    nodes.push({ node, start: text.length, end: text.length + node.data.length });
    text += node.data;
  }
  return { nodes, text };
}

function matches(text: string, annotations: Annotation[]) {
  const flat = normalized(text);
  return annotations.flatMap(annotation => {
    const quote = normalized(annotation.quote).value.trim();
    if (!quote) return [];
    const hits: { start: number; end: number; annotation: Annotation }[] = [];
    for (let from = 0; from < flat.value.length;) {
      const index = flat.value.indexOf(quote, from);
      if (index < 0) break;
      hits.push({ start: flat.starts[index], end: flat.ends[index + quote.length - 1], annotation });
      from = index + 1; // all occurrences, including overlapping short quotes
    }
    return hits;
  });
}

export function locatedAnnotations(root: HTMLElement, annotations: Annotation[]): Set<string> {
  return new Set(matches(textNodes(root).text, annotations).map(hit => hit.annotation.id));
}

export function clearHighlights(root: HTMLElement) {
  for (const mark of root.querySelectorAll("mark[data-annotation-ids]")) mark.replaceWith(...mark.childNodes);
  root.normalize();
}

export function highlightAnnotations(root: HTMLElement, annotations: Annotation[]) {
  clearHighlights(root);
  const { nodes, text } = textNodes(root);
  const hits = matches(text, annotations);
  for (const { node, start, end } of nodes) {
    const overlaps = hits.filter(hit => hit.start < end && hit.end > start);
    if (!overlaps.length) continue;
    const edges = [...new Set([start, end, ...overlaps.flatMap(hit => [Math.max(start, hit.start), Math.min(end, hit.end)])])].sort((a, b) => a - b);
    const fragment = document.createDocumentFragment();
    for (let i = 0; i < edges.length - 1; i++) {
      const a = edges[i], b = edges[i + 1];
      const notes = overlaps.filter(hit => hit.start < b && hit.end > a).map(hit => hit.annotation);
      const content = text.slice(a, b);
      if (!notes.length) { fragment.append(document.createTextNode(content)); continue; }
      const mark = document.createElement("mark");
      mark.className = "rd-annotation-highlight";
      mark.dataset.annotationIds = JSON.stringify([...new Set(notes.map(note => note.id))]);
      mark.tabIndex = 0;
      mark.setAttribute("aria-label", `${content} — ${notes.map(note => note.note).join("; ")}`);
      mark.textContent = content;
      fragment.append(mark);
    }
    node.replaceWith(fragment);
  }
}
