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
      };
    };
  }
}

export function desktop(): Desktop | undefined {
  return window.__rubbertermDesktop;
}

export function selectLaunchTarget(target: string, draft: LaunchDraft): void {
  const bridge = window.webkit?.messageHandlers?.remoteSession;
  if (!bridge) throw new Error("Open RubberTerm to choose another computer");
  bridge.postMessage({ action: "launch", target, draft });
}
