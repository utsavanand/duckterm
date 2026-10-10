import { afterEach, expect, it, vi } from "vitest";
import type { Terminal } from "@xterm/xterm";
import { bindClipboardBridge, releaseClipboardBridge } from "./clipboardBridge";
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
