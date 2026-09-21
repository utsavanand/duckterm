export type LaunchDraft = { agent: string; command: string; name: string; prompt: string };

type Desktop = {
  currentTarget: string;
  targets: { id: string; name: string }[];
  draft?: LaunchDraft;
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

export function selectLaunchTarget(target: string, draft: LaunchDraft | Record<string, never>): void {
  const bridge = window.webkit?.messageHandlers?.remoteSession;
  if (!bridge) throw new Error("Open RubberTerm to choose another computer");
  bridge.postMessage({ action: "launch", target, draft });
}

export async function destinationRequest<T>(target: string, operation: "browse" | "branches" | "themes" | "launch", params: object = {}): Promise<T> {
  const bridge = window.webkit?.messageHandlers?.launchRequest;
  if (!bridge) throw new Error("Update RubberTerm Test to browse another computer without switching screens");
  return await bridge.postMessage({ target, operation, params }) as T;
}
