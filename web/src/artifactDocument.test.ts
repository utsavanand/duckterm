import { beforeEach, expect, it, vi } from "vitest";
import { diagramPreviewDocument, previewDocument } from "./artifactDocument";
import { renderDiagram } from "./renderDiagram";
vi.mock("./renderDiagram", () => ({ renderDiagram: vi.fn() }));
const bridge = { nonce: "trusted", origin: "http://localhost:4300" };
beforeEach(() => vi.resetAllMocks());
it("renders sanitized SVG before the frame without authorizing artifact scripts or navigation", async () => {
  vi.mocked(renderDiagram).mockResolvedValue('<svg viewBox="0 0 400 200" onload="evil()"><script>evil()</script><foreignObject><p>bad</p></foreignObject><a href="https://example.invalid"><text>Visible label</text></a></svg>');
  const doc = await diagramPreviewDocument('```mermaid\nflowchart LR\nA --> B\n```', bridge, "dark", () => false);
  expect(renderDiagram).toHaveBeenCalledWith("flowchart LR\nA --> B", true, true);
  expect(doc).toContain('width="400"');
  expect(doc).toContain("Visible label");
  expect(doc).not.toMatch(/foreignObject|onload|href=|evil\(\)/);
  expect(doc.match(/<script/g)).toHaveLength(1);
  expect(doc).toContain("default-src 'none'; script-src 'nonce-trusted'");
});
it("keeps failed diagram source readable and continues to the next diagram", async () => {
  vi.mocked(renderDiagram).mockRejectedValueOnce(new Error("invalid")).mockResolvedValueOnce('<svg viewBox="0 0 80 40"><text>OK</text></svg>');
  const doc = await diagramPreviewDocument('```mermaid\nbad input\n```\n\n```mermaid\nflowchart LR\nA --> B\n```', bridge, "light", () => false);
  expect(doc).toContain("Diagram could not render. Source shown below.");
  expect(doc).toContain("bad input");
  expect(doc).toContain('<text>OK</text>');
});
it("does not queue further diagrams after an artifact is deselected", async () => {
  let cancel = false;
  vi.mocked(renderDiagram).mockImplementation(async () => { cancel = true; return '<svg viewBox="0 0 80 40"><text>Old</text></svg>'; });
  const doc = await diagramPreviewDocument('```mermaid\nA\n```\n\n```mermaid\nB\n```', bridge, "light", () => cancel);
  expect(renderDiagram).toHaveBeenCalledTimes(1);
  expect(doc).not.toContain('<text>Old</text>');
});
it("provides scrollable tables and unwrapped code before diagrams load, and leaves HTML styles alone", () => {
  const doc = previewDocument('| A | B |\n| - | - |\n| Long text | Cell |\n\n```text\nA    B\n```', true, undefined, "dark");
  expect(doc).toContain('class="table-scroll"');
  expect(doc).toContain('tabindex="0"');
  expect(doc).toContain("color-scheme:dark");
  expect(doc).toContain("A    B");
  expect(previewDocument('<p>Custom HTML</p>', false, undefined, "dark")).not.toContain("--paper");
});
