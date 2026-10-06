// Mermaid configuration is global. Serialize configuration + rendering across
// Messages and Artifacts so simultaneous dark/light renders cannot interfere.
let sequence = 0;
let pending: Promise<unknown> = Promise.resolve();
export function renderDiagram(source: string, dark: boolean, svgLabels = false): Promise<string> {
  const result = pending.then(async () => {
    const { default: mermaid } = await import("mermaid");
    mermaid.initialize({ startOnLoad: false, securityLevel: "strict", theme: dark ? "dark" : "default", htmlLabels: !svgLabels, suppressErrorRendering: true });
    const { svg } = await mermaid.render(`rd-diagram-${++sequence}`, source);
    return svg;
  });
  pending = result.catch(() => undefined);
  return result;
}
