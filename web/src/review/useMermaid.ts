import { useEffect, type RefObject } from "react";

let sequence = 0;

// Review diagrams are written by a model reading a diff, or built from symbol
// names in it, so a prompt injection can reach them. Mermaid copies init
// directives (themeCSS), frontmatter config and classDef/style values into the
// SVG's <style>, and its CSS sanitizer keeps url() (security on #509). Such
// diagrams stay as readable source; nothing a diagram needs is lost.
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

// Render `pre.rd-mermaid` blocks inside a container into SVG. The mermaid
// chunk loads lazily, as Messages does; a failed or refused render keeps the source.
export function useMermaid(container: RefObject<HTMLElement | null>, dependency: unknown) {
  useEffect(() => {
    const blocks = container.current?.querySelectorAll<HTMLElement>("pre.rd-mermaid:not([data-rendered])");
    if (!blocks || blocks.length === 0) return;
    let live = true;
    import("mermaid").then(async ({ default: mermaid }) => {
      if (!live) return;
      const dark = document.documentElement.getAttribute("data-theme") !== "light";
      mermaid.initialize({ startOnLoad: false, securityLevel: "strict", theme: dark ? "dark" : "default",
        secure: ["secure", "securityLevel", "startOnLoad", "maxTextSize", "maxEdges", "themeCSS", "themeVariables", "fontFamily"] });
      for (const el of [...blocks]) {
        el.setAttribute("data-rendered", "1");
        const source = el.textContent ?? "";
        if (!diagramSourceSafe(source)) continue;
        try {
          const { svg } = await mermaid.render(`rdr-${++sequence}`, source);
          if (!diagramSvgSafe(svg)) continue;
          const host = document.createElement("div");
          host.className = "rd-mermaid-svg";
          host.innerHTML = svg;
          el.replaceWith(host);
        } catch { /* invalid diagram: keep the readable source */ }
      }
    }).catch(() => { /* chunk unavailable: keep the source */ });
    return () => { live = false; };
  }, [container, dependency]);
}
