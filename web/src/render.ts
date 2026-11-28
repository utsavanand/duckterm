import DOMPurify from "dompurify";
import hljs from "highlight.js/lib/core";
import bash from "highlight.js/lib/languages/bash";
import css from "highlight.js/lib/languages/css";
import diff from "highlight.js/lib/languages/diff";
import go from "highlight.js/lib/languages/go";
import javascript from "highlight.js/lib/languages/javascript";
import json from "highlight.js/lib/languages/json";
import python from "highlight.js/lib/languages/python";
import rust from "highlight.js/lib/languages/rust";
import sql from "highlight.js/lib/languages/sql";
import typescript from "highlight.js/lib/languages/typescript";
import xml from "highlight.js/lib/languages/xml";
import yaml from "highlight.js/lib/languages/yaml";
import { marked } from "marked";

// Markdown -> sanitized HTML for the Messages view, with syntax-highlighted
// code fences. hljs CORE + a hand-picked language set keeps the bundle small
// (the full build ships 190+ grammars); a fence in an unregistered language
// renders as plain escaped code rather than paying for auto-detection.
hljs.registerLanguage("bash", bash);
hljs.registerAliases(["sh", "shell", "zsh"], { languageName: "bash" });
hljs.registerLanguage("css", css);
hljs.registerLanguage("diff", diff);
hljs.registerLanguage("go", go);
hljs.registerLanguage("javascript", javascript);
hljs.registerAliases(["js", "jsx"], { languageName: "javascript" });
hljs.registerLanguage("json", json);
hljs.registerLanguage("python", python);
hljs.registerAliases(["py"], { languageName: "python" });
hljs.registerLanguage("rust", rust);
hljs.registerLanguage("sql", sql);
hljs.registerLanguage("typescript", typescript);
hljs.registerAliases(["ts", "tsx"], { languageName: "typescript" });
hljs.registerLanguage("xml", xml);
hljs.registerAliases(["html"], { languageName: "xml" });
hljs.registerLanguage("yaml", yaml);
hljs.registerAliases(["yml"], { languageName: "yaml" });

function escapeHtml(s: string): string {
  return s
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;");
}

marked.use({
  renderer: {
    code({ text, lang }: { text: string; lang?: string }) {
      // Mermaid fences render as diagrams client-side (Messages lazy-loads
      // the mermaid chunk only when one exists). Emit the source escaped in
      // a tagged block; invalid diagrams stay readable as code.
      if (lang === "mermaid") {
        return `<pre class="rd-mermaid"><code>${escapeHtml(text)}</code></pre>\n`;
      }
      const language = lang && hljs.getLanguage(lang) ? lang : null;
      const body = language
        ? hljs.highlight(text, { language }).value
        : escapeHtml(text);
      return `<pre><code class="hljs">${body}</code></pre>\n`;
    },
  },
});

// The same registered grammars, for views that highlight code line by line.
export const highlighter = hljs;

export function html(md: string): string {
  return DOMPurify.sanitize(marked.parse(md, { async: false }) as string);
}

// Text a model or agent wrote (owner: "do the strict filter everywhere"). It
// reads content someone else controls, and a PR diff or a web page can carry
// a prompt injection. Rendering it must not fetch anything: a remote image,
// an <input type=image> or a style url() would carry what the model was told
// to leak in its URL. An allowlist of what markdown produces, because a
// denylist missed <input type=image> and <mglyph src> (security, QA on #509).
const MODEL_TAGS = ["p", "br", "hr", "strong", "b", "em", "i", "del", "s", "code", "pre", "kbd", "sup", "sub", "blockquote",
  "ul", "ol", "li", "a", "h1", "h2", "h3", "h4", "h5", "h6", "table", "thead", "tbody", "tr", "th", "td", "span", "div"];
const MODEL_ATTRS = ["href", "title", "class", "start", "align"];

export function modelHtml(md: string): string {
  return DOMPurify.sanitize(marked.parse(md, { async: false }) as string, {
    ALLOWED_TAGS: MODEL_TAGS, ALLOWED_ATTR: MODEL_ATTRS, ALLOW_DATA_ATTR: false,
  });
}

// Diagrams in model and agent text (review cards, transcripts) can carry a
// prompt injection. Mermaid copies init directives (themeCSS), frontmatter
// config and classDef/style values into the SVG's <style>, and its CSS
// sanitizer keeps url() (security on #509). Such diagrams stay as readable
// source; nothing a diagram needs is lost.
const STYLING = /%%\{|^\s*---|^\s*(classDef|style|linkStyle)\s/im;
export function diagramSourceSafe(source: string): boolean {
  return !STYLING.test(source);
}

// After rendering, anything that could still fetch: url() other than the
// SVG's own #marker references, @import, image-set, CSS escapes in a <style>
// block or style attribute (which could spell url), and href/src pointing
// outside the SVG.
const CSS_TEXT = /<style[^>]*>([\s\S]*?)<\/style>|\sstyle\s*=\s*"([^"]*)"|\sstyle\s*=\s*'([^']*)'/gi;
const FETCHING = /url\(\s*['"]?\s*(?!#)|@import|image-set\(|\s(?:xlink:)?(?:href|src)\s*=\s*['"]?\s*(?!#)/i;
export function diagramSvgSafe(svg: string): boolean {
  if (FETCHING.test(svg)) return false;
  return ![...svg.matchAll(CSS_TEXT)].some(match => (match[1] ?? match[2] ?? match[3]).includes("\\"));
}

// Config keys diagram text may not set: the ones mermaid copies into CSS.
export const MERMAID_SECURE = ["secure", "securityLevel", "startOnLoad", "maxTextSize", "maxEdges", "themeCSS", "themeVariables", "fontFamily"];
