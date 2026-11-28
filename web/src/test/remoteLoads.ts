// Model text carrying every remote-loading form security and QA found, each
// pointing at a reserved host, plus formatting that must still render.
export const LEAKY = [
  "**Leak check** with ![md](https://leak.invalid/markdown)",
  '<img src="https://leak.invalid/img"> <input type="image" src="https://leak.invalid/input">',
  '<p style="background:url(https://leak.invalid/style)">kept</p> <math><mglyph src="https://leak.invalid/mglyph"></mglyph></math>',
].join("\n\n");

// Every attribute in the rendered DOM that still names the leak host.
export function remoteRefs(root: ParentNode): string[] {
  return [...root.querySelectorAll("*")].flatMap(el => [...el.attributes].filter(a => a.value.includes("leak.invalid")).map(a => `<${el.tagName.toLowerCase()} ${a.name}>`));
}
