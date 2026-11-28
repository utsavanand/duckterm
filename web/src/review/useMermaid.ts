import { useEffect, type RefObject } from "react";
import { diagramSourceSafe, diagramSvgSafe, MERMAID_SECURE } from "../render";

let sequence = 0;

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
        secure: MERMAID_SECURE });
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
