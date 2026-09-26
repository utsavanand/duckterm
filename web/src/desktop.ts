export type LaunchDraft = { agent: string; command: string; name: string; prompt: string };

type Desktop = {
  currentTarget: string;
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
  const bridge = window.webkit?.messageHandlers?.remoteSession;
  if (!bridge) throw new Error("Open RubberTerm to choose another computer");
  bridge.postMessage({ action: "launch", target, draft, ...(sessionKey ? { session_key: sessionKey } : {}) });
}

export async function destinationRequest<T>(target: string, operation: "browse" | "branches" | "themes" | "launch" | "project-preview" | "project-transfer" | "project-clone" | "project-launch" | "project-status" | "project-pause" | "project-preflight" | "project-continue", params: object = {}): Promise<T> {
  const bridge = window.webkit?.messageHandlers?.launchRequest;
  if (!bridge) throw new Error("Update RubberTerm Test to browse another computer without switching screens");
  return await bridge.postMessage({ target, operation, params }) as T;
}
