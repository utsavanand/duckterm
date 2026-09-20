import type { Terminal as XTerm } from "@xterm/xterm";

// Clipboard bridge for the Mac app (RubberTerm.app). WKWebView validates the
// standard Edit-menu Copy item against the DOM selection — an xterm selection
// is canvas-rendered and has none, so Cmd+C stayed disabled forever. The app's
// Copy/Paste menu items call these globals instead; browsers never use them
// (xterm's native copy event and DOM paste work there).
let active: XTerm | null = null;

export function bindClipboardBridge(term: XTerm): void {
  const claim = () => {
    active = term;
  };
  term.textarea?.addEventListener("focus", claim);
  claim();
}

export function releaseClipboardBridge(term: XTerm): void {
  if (active === term) active = null;
}

function editableField(): HTMLInputElement | HTMLTextAreaElement | null {
  const el = document.activeElement;
  const isField =
    el instanceof HTMLInputElement || el instanceof HTMLTextAreaElement;
  // xterm's hidden helper textarea is a field too — that one means "the
  // terminal", not a form input.
  return isField && !el.closest(".xterm") ? el : null;
}

declare global {
  interface Window {
    __rtCopy?: () => string;
    __rtPaste?: (text: string) => void;
  }
}

window.__rtCopy = () => {
  const field = editableField();
  if (field) {
    return field.value.slice(
      field.selectionStart ?? 0,
      field.selectionEnd ?? 0,
    );
  }
  if (active?.hasSelection()) return active.getSelection();
  return String(document.getSelection() ?? "");
};

window.__rtPaste = (text: string) => {
  if (editableField()) {
    document.execCommand("insertText", false, text);
    return;
  }
  active?.paste(text);
};
