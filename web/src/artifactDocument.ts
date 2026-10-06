import DOMPurify from "dompurify";
import { html } from "./render";
import { renderDiagram } from "./renderDiagram";
import paper from "./artifactPaper.css?raw";

const palette = {"dark": "--paper:#101419;--text:#e2e6e3;--muted:#a7b3aa;--line:#39473e;--code:#1a221e;--stripe:#161d19;--accent:#61d69a;", "light": "--paper:#fafbf8;--text:#27352c;--muted:#526658;--line:#b7c7bb;--code:#eaf0e9;--stripe:#eff3ec;--accent:#14643b;"};

const POLICY = `<meta http-equiv="Content-Security-Policy" content="default-src 'none'; img-src data: blob:; style-src 'unsafe-inline'; font-src data:; form-action 'none'; base-uri 'none'">`;

export type PreviewBridge = { nonce: string; origin: string };

export function previewDocument(source: string, markdown: boolean, bridge?: PreviewBridge, theme: "dark" | "light" = "light"): string {
  return wrapDocument(markdown ? markdownBody(source).innerHTML : source, markdown, bridge, theme);
}

function wrapDocument(source: string, markdown: boolean, bridge: PreviewBridge | undefined, theme: "dark" | "light"): string {
  const clean = DOMPurify.sanitize(source, {
    WHOLE_DOCUMENT: !markdown,
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
    document.addEventListener('keydown', event => { if (event.key === 'Escape') parent.postMessage({type: 'artifact-escape', channel: ${JSON.stringify(bridge.nonce)}}, ${JSON.stringify(bridge.origin)}); });
    document.addEventListener('mouseup', report);
    document.addEventListener('keyup', event => { if (event.key === 'Shift' || event.key.startsWith('Arrow')) report(); });
  })();</script>` : "";
  return `<!doctype html>${policy}${markdown ? `<style>:root{color-scheme:${theme};${palette[theme]}}${paper}</style>` : ""}${markdown ? `<article>${clean}</article>` : clean}${reporter}`;
}

function markdownBody(source: string): HTMLDivElement {
  // Parse in an inert document: detached nodes in the live document can still
  // fetch image URLs before the final iframe CSP exists.
  const root = document.implementation.createHTMLDocument("").createElement("div");
  root.innerHTML = html(source);
  for (const table of root.querySelectorAll("table")) {
    const wrapper = root.ownerDocument.createElement("div");
    wrapper.className = "table-scroll";
    wrapper.tabIndex = 0;
    wrapper.setAttribute("role", "region");
    wrapper.setAttribute("aria-label", "Table — scroll horizontally if needed");
    table.replaceWith(wrapper);
    wrapper.append(table);
  }
  for (const pre of root.querySelectorAll("pre")) pre.tabIndex = 0;
  return root;
}

export async function diagramPreviewDocument(source: string, bridge: PreviewBridge, theme: "dark" | "light", cancelled: () => boolean): Promise<string> {
  const root = markdownBody(source);
  for (const code of root.querySelectorAll("pre.rd-mermaid")) {
    if (cancelled()) break;
    try {
      const svg = await renderDiagram(code.textContent ?? "", theme === "dark", true);
      if (cancelled()) break;
      const box = root.ownerDocument.createElement("div");
      box.className = "diagram";
      box.tabIndex = 0;
      box.setAttribute("role", "region");
      box.setAttribute("aria-label", "Diagram");
      box.innerHTML = DOMPurify.sanitize(svg, { USE_PROFILES: { svg: true, svgFilters: true }, FORBID_TAGS: ["foreignObject"], FORBID_ATTR: ["href", "xlink:href", "target"] });
      const rendered = box.querySelector("svg");
      if (!rendered) throw new Error("No diagram");
      const bounds = rendered.getAttribute("viewBox")?.trim().split(/\s+/).map(Number);
      if (bounds?.length === 4 && bounds.every(Number.isFinite) && bounds[2] > 0 && bounds[3] > 0) {
        rendered.setAttribute("width", String(bounds[2]));
        rendered.setAttribute("height", String(bounds[3]));
      }
      code.replaceWith(box);
    } catch {
      const label = root.ownerDocument.createElement("p");
      label.className = "fallback-label";
      label.textContent = "Diagram could not render. Source shown below.";
      code.before(label);
    }
  }
  return wrapDocument(root.innerHTML, true, bridge, theme);
}
