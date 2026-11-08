import { afterEach, expect, it, vi } from "vitest";
import type { Terminal } from "@xterm/xterm";
import { bindClipboardBridge, bindSetupClipboard, bindTerminalClipboard, releaseClipboardBridge } from "./clipboardBridge";
import { sessionFetch, sessionRef } from "./hostTransport";
vi.mock("./hostTransport", async importOriginal => ({ ...await importOriginal<typeof import("./hostTransport")>(), sessionFetch: vi.fn() }));
const terminals: Terminal[] = [];
function terminal(key: string, kind: "agent" | "shell") {
  const node = document.createElement("div"); node.className = "xterm";
  const textarea = document.createElement("textarea"); node.append(textarea); document.body.append(node);
  textarea.getClientRects = () => [{ width: 10 }] as unknown as DOMRectList;
  const term = { textarea, paste: vi.fn() } as unknown as Terminal;
  terminals.push(term); bindClipboardBridge(term, key, kind); textarea.focus(); return term;
}
afterEach(() => { terminals.forEach(releaseClipboardBridge); terminals.length = 0; document.body.innerHTML = ""; vi.restoreAllMocks(); });
it("does not paste a resolved image into the sibling terminal of the same session", async () => {
  const key = sessionRef("remote", "same");
  const agent = terminal(key, "agent");
  const agentTarget = window.__rtPasteTarget!()!;
  const shell = terminal(key, "shell");
  const shellTarget = window.__rtPasteTarget!()!;
  expect(shellTarget).not.toBe(agentTarget);
  expect(shellTarget.startsWith("~remote~")).toBe(true);
  expect(window.__rtPasteImage!("/tmp/image.png", agentTarget)).toBe(false);
  let resolve!: (value: Response) => void;
  vi.mocked(sessionFetch).mockReturnValue(new Promise(done => { resolve = done; }));
  const alert = vi.spyOn(window, "alert").mockImplementation(() => {});
  expect(window.__rtPasteImageData!(btoa("png"), shellTarget)).toBe(true);
  expect(sessionFetch).toHaveBeenCalledWith(key, "/paste-image", expect.any(Object));
  agent.textarea!.focus();
  resolve(new Response('{"path":"/remote/image.png"}'));
  await vi.waitFor(() => expect(alert).toHaveBeenCalled());
  expect(agent.paste).not.toHaveBeenCalled(); expect(shell.paste).not.toHaveBeenCalled();
});

it("only copies bounded live OSC52 writes from the focused terminal", () => {
  const term = terminal("remote", "agent");
  let handler!: (data: string) => boolean;
  Object.assign(term, { parser: { registerOscHandler: (_: number, callback: typeof handler) => { handler = callback; return { dispose() {} }; } } });
  const postMessage = vi.fn();
  window.webkit = { messageHandlers: { remoteSession: { postMessage } } };
  let live = false;
  const binding = bindTerminalClipboard(term, () => live);
  const payload = "c;" + btoa("https://example.test/oauth?synthetic=1");
  handler(payload); expect(postMessage).not.toHaveBeenCalled();
  live = true;
  handler(payload);
  expect(postMessage).toHaveBeenCalledWith({ action: "copy-terminal-text", text: "https://example.test/oauth?synthetic=1" });
  postMessage.mockClear();
  for (const invalid of ["c;?", "c;!", "c;" + "YQ==".repeat(22000), "c;/w=="]) handler(invalid);
  expect(postMessage).not.toHaveBeenCalled();
  terminal("other", "agent"); handler(payload);
  expect(postMessage).not.toHaveBeenCalled();
  binding.dispose(); delete window.webkit;
});

it("routes setup copy/paste only to its focused ephemeral terminal and refuses images", () => {
  const old = terminal("existing-agent", "agent");
  Object.assign(old, { hasSelection: () => true, getSelection: () => "old agent selection" });
  const node = document.createElement("div"); node.className = "xterm";
  const textarea = document.createElement("textarea"); node.append(textarea); document.body.append(node);
  textarea.getClientRects = () => [{ width: 10 }] as unknown as DOMRectList;
  const ephemeral = { textarea, paste: vi.fn(), hasSelection: () => true, getSelection: () => "synthetic sign-in code" } as unknown as Terminal;
  let enabled = true;
  const release = bindSetupClipboard(ephemeral, () => enabled);
  const readsBefore = vi.mocked(sessionFetch).mock.calls.length;
  try {
    textarea.focus();
    expect(window.__rtCopy!()).toBe("synthetic sign-in code");
    expect(window.__rtPasteTarget!()).toBe("runtime-setup");
    expect(window.__rtPasteSetup!("test code")).toBe(true);
    expect(ephemeral.paste).toHaveBeenCalledWith("test code");
    expect(window.__rtPasteImage!("/tmp/no-save.png", "runtime-setup")).toBe(false);
    expect(window.__rtPasteImageData!(btoa("png"), "runtime-setup")).toBe(false);
    expect(vi.mocked(sessionFetch).mock.calls.length).toBe(readsBefore);
    enabled = false;
    expect(window.__rtPasteTarget!()).toBeNull();
    expect(window.__rtPasteSetup!("do not send")).toBe(false);
    window.__rtPaste!("do not send");
    expect(ephemeral.paste).toHaveBeenCalledTimes(1);
    enabled = true; old.textarea!.focus();
    expect(window.__rtPasteSetup!("stale paste")).toBe(false);
    expect(old.paste).not.toHaveBeenCalled();
    expect(window.__rtCopy!()).toBe("old agent selection");
    textarea.focus(); release();
    expect(window.__rtPasteSetup!("closed setup")).toBe(false);
  } finally { release(); }
});
