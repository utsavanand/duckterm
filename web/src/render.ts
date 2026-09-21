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

export function html(md: string): string {
  return DOMPurify.sanitize(marked.parse(md, { async: false }) as string);
}
