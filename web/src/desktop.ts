import { sessionRef } from "./hostTransport";
export type LaunchDraft = { agent: string; command: string; name: string; prompt: string };

type Desktop = {
  currentTarget: string;
  launchTarget?: string;
  testBuild?: boolean;
  canReportBug?: boolean;
  canCollaborate?: boolean;
  targets: { id: string; name: string }[];
  draft?: LaunchDraft;
  selectedSession?: string;
};

declare global {
  interface Window {
    __rubbertermDesktop?: Desktop;
    webkit?: {
      messageHandlers?: {
        remoteSession?: { postMessage: (message: unknown) => void };
        launchRequest?: { postMessage: (message: unknown) => Promise<unknown> };
      };
    };
  }
}

export function desktop(): Desktop | undefined {
  return window.__rubbertermDesktop;
}

export function selectLaunchTarget(target: string, draft: LaunchDraft | Record<string, never>, sessionKey?: string): void {
  if (sessionKey && target !== "add") {
    window.dispatchEvent(new CustomEvent("select-host-session", { detail: sessionRef(target, sessionKey) }));
    window.dispatchEvent(new Event("remote-sessions-refresh"));
    return;
  }
  const bridge = window.webkit?.messageHandlers?.remoteSession;
  if (!bridge) throw new Error("Open RubberTerm to choose another computer");
  bridge.postMessage({ action: "launch", target, draft, ...(sessionKey ? { session_key: sessionKey } : {}) });
}

export async function destinationRequest<T>(target: string, operation: "collaboration-folder" | "collaboration-retry" | "collaboration-cancel" | "collaboration-status" | "collaboration-move" | "collaboration-preview" | "collaboration-connect" | "session-request" | "terminal-open" | "terminal-send" | "terminal-close" | "browse" | "branches" | "themes" | "launch" | "project-mkdir" | "project-repositories" | "project-preview" | "project-transfer" | "project-clone" | "project-launch" | "project-status" | "project-pause" | "project-preflight" | "project-continue", params: object = {}): Promise<T> {
  const bridge = window.webkit?.messageHandlers?.launchRequest;
  if (!bridge) throw new Error("Update RubberTerm Test to browse another computer without switching screens");
  return await bridge.postMessage({ target, operation, params }) as T;
}

// Older Mac builds have the host bridge but no native reporter action.
// Feature detection preserves their usable browser report flow.
export function openNativeBugReport(): boolean {
  const bridge = window.webkit?.messageHandlers?.remoteSession;
  if (!desktop()?.canReportBug || !bridge) return false;
  bridge.postMessage({ action: "report-bug" });
  return true;
}
