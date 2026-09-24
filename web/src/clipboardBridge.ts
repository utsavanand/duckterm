import type { Terminal as XTerm } from "@xterm/xterm";

// Clipboard bridge for the Mac app (DuckTerm.app). WKWebView validates the
// standard Edit-menu Copy item against the DOM selection — an xterm selection
// is canvas-rendered and has none, so Cmd+C stayed disabled forever. The app's
// Copy/Paste menu items call these globals instead; browsers never use them
// (xterm's native copy event and DOM paste work there).
let active: XTerm | null = null;
let activeSession: string | null = null;

export function bindClipboardBridge(term: XTerm, sessionKey: string): void {
  const claim = () => {
    active = term;
    activeSession = sessionKey;
  };
  term.textarea?.addEventListener("focus", claim);
  if (document.activeElement === term.textarea) claim();
}

export function releaseClipboardBridge(term: XTerm): void {
  if (active === term) { active = null; activeSession = null; }
}

function editableField(): HTMLElement | null {
  const el = document.activeElement;
  const isField =
    el instanceof HTMLInputElement || el instanceof HTMLTextAreaElement || (el instanceof HTMLElement && el.isContentEditable);
  // xterm's hidden helper textarea is a field too — that one means "the
  // terminal", not a form input.
  return isField && !el.closest(".xterm") ? el : null;
}

declare global {
  interface Window {
    __rtPasteTarget?: () => string | null;
    __rtPasteImage?: (path: string, sessionKey: string) => boolean;
    __rtCopy?: () => string;
    __rtPaste?: (text: string) => void;
  }
}

window.__rtCopy = () => {
  const field = editableField();
  if (field instanceof HTMLInputElement || field instanceof HTMLTextAreaElement) {
    return field.value.slice(
      field.selectionStart ?? 0,
      field.selectionEnd ?? 0,
    );
  }
  if (field) return String(document.getSelection() ?? "");
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

// Native image resolution is only appropriate for a visible, focused terminal.
window.__rtPasteTarget = () => {
  if (editableField()) return "field";
  return active?.textarea === document.activeElement && active?.textarea?.getClientRects().length ? activeSession : null;
};
window.__rtPasteImage = (path, sessionKey) => {
  if (window.__rtPasteTarget?.() !== sessionKey) return false;
  active?.paste(imagePathText(path));
  return true;
};

export function imagePathText(path: string): string {
  // Quote paths with spaces/metacharacters; never reduce a path to its basename.
  return (/^[\w/.-]+$/.test(path) ? path : "'" + path.replace(/'/g, "'\\''") + "'") + " ";
}
