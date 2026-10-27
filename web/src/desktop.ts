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

// null until the edition loads. The edition decides whether other computers
// exist for this dashboard; every host list, launch target and remote poll
// reads them through desktop().
let remoteWorkspaces: boolean | null = null;

export function allowRemoteWorkspaces(allowed: boolean | null): void {
  if (allowed === remoteWorkspaces) return;
  remoteWorkspaces = allowed;
  window.dispatchEvent(new Event("desktop-targets-changed"));
}

// A computer the Mac app knows but desktop() hides: "pending" until the edition loads.
export function hiddenHost(host: string): "pending" | "hidden" | null {
  if (remoteWorkspaces === true || !window.__rubbertermDesktop?.targets.some(t => t.id === host)) return null;
  return remoteWorkspaces === null ? "pending" : "hidden";
}

export function desktop(): Desktop | undefined {
  const bridge = window.__rubbertermDesktop;
  if (!bridge || remoteWorkspaces === true) return bridge;
  return { ...bridge, currentTarget: "local", launchTarget: undefined, targets: bridge.targets.filter(t => t.id === "local") };
}

export function clearLaunchDraft(): void {
  delete window.__rubbertermDesktop?.draft;
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

export async function destinationRequest<T>(target: string, operation: "collaboration-folder" | "collaboration-retry" | "collaboration-cancel" | "collaboration-keep-separately" | "collaboration-disconnect" | "collaboration-status" | "collaboration-move" | "collaboration-preview" | "collaboration-connect" | "session-request" | "terminal-open" | "terminal-send" | "terminal-close" | "browse" | "branches" | "themes" | "opencode-options" | "launch" | "project-mkdir" | "project-repositories" | "project-preview" | "project-transfer" | "project-clone" | "project-launch" | "project-status" | "project-pause" | "project-preflight" | "project-continue", params: object = {}): Promise<T> {
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
