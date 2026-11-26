// Shared with the isolated frame by serializing this dependency-free function.
export function locateFileQuote(root: HTMLElement, quote: string): string {
  const nodes: Text[] = [];
  const walker = root.ownerDocument.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  let node;
  while ((node = walker.nextNode())) {
    if (!(node.parentElement?.closest("script,style,noscript,[hidden]"))) nodes.push(node as Text);
  }
  const text = nodes.map(n => n.textContent ?? "").join("");
  const start = text.indexOf(quote);
  if (!quote || start < 0) return "This quote is not present in this view. Try the other reader mode.";
  if (text.indexOf(quote, start + 1) >= 0) return "This quote appears more than once. No location was selected.";
  const range = root.ownerDocument.createRange();
  let offset = 0, began = false;
  for (const item of nodes) {
    const end = offset + item.length;
    if (!began && start < end) { range.setStart(item, start - offset); began = true; }
    if (began && start + quote.length <= end) { range.setEnd(item, start + quote.length - offset); break; }
    offset = end;
  }
  const selection = root.ownerDocument.defaultView?.getSelection();
  selection?.removeAllRanges(); selection?.addRange(range);
  range.startContainer.parentElement?.scrollIntoView({ block: "center" });
  return "Quote selected in this version.";
}
