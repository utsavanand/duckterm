import { describe, expect, it } from "vitest";
import { diagramSourceSafe, diagramSvgSafe } from "./useMermaid";

describe("review diagrams load nothing remote (security on #509)", () => {
  it("renders the diagrams the review writes", () => {
    expect(diagramSourceSafe('graph LR\n  n0["readinessRows"]\n  n2["Boards.tsx"] --> n0')).toBe(true);
    expect(diagramSourceSafe("graph LR\n  subgraph web\n    A[ReviewV2] --> B(readiness.ts)\n  end\n  B -->|CI, gate| C{status}")).toBe(true);
  });

  it.each([
    ["an init directive", '%%{init: {"themeCSS": ".node span{background-image:url(https://evil.example/a)}"}}%%\ngraph LR\n  A --> B'],
    ["frontmatter config", '---\nconfig:\n  themeCSS: ".node{background:url(https://evil.example/d)}"\n---\ngraph LR\n  A --> B'],
    ["a classDef", "graph LR\n  A --> B\n  classDef x background-image:url(https://evil.example/b)"],
    ["a style line", "graph LR\n  A --> B\n  style A background-image:url(https://evil.example/c)"],
    ["a linkStyle", "graph LR\n  A --> B\n  linkStyle 0 stroke:red"],
  ])("keeps a diagram with %s as source", (_, source) => {
    expect(diagramSourceSafe(source)).toBe(false);
  });

  // Shaped like mermaid 12.2's flowchart output: namespaces, marker refs, its own CSS.
  const real = '<svg id="rdr-1" xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink"><style>#rdr-1{font-family:"trebuchet ms",verdana,arial,sans-serif;}</style>'
    + '<path marker-end="url(#rdr-1_flowchart-v2-pointEnd)" style="fill:none;"/></svg>';
  it("accepts mermaid's own markup", () => {
    expect(diagramSvgSafe(real)).toBe(true);
  });

  it.each([
    ["a CSS url", real.replace("</style>", ".node{background:url(https://evil.example/e)}</style>")],
    ["an @import", real.replace("</style>", '@import "https://evil.example/f";</style>')],
    ["an image-set", real.replace("</style>", ".n{background:image-set('x.png' 1x)}</style>")],
    ["a CSS escape in a style block", real.replace("</style>", ".n{background:u\\72l(//evil.example/g)}</style>")],
    ["a CSS escape in a style attribute", real.replace('style="fill:none;"', 'style="background:u\\72l(//evil.example/h)"')],
    ["an outside href", real.replace("</svg>", '<image href="https://evil.example/i"/></svg>')],
    ["an outside xlink:href", real.replace("</svg>", '<use xlink:href="//evil.example/j#x"/></svg>')],
  ])("refuses an SVG with %s", (_, svg) => {
    expect(diagramSvgSafe(svg)).toBe(false);
  });
});
