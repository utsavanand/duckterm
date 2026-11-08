import { sessionRef } from "./hostTransport";
export type LaunchDraft = { agent: string; command: string; name: string; prompt: string };

type Desktop = {
  currentTarget: string;
  launchTarget?: string;
  testBuild?: boolean;
  canReportBug?: boolean;
  canCollaborate?: boolean;
  canApprove?: boolean;
  canSetUpAgents?: boolean;
  agentSetup?: RuntimeSetupState;
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
        approval?: { postMessage: (message: unknown) => Promise<unknown> };
        runtimeSetup?: { postMessage: (message: unknown) => Promise<unknown> };
      };
    };
  }
}

export type SetupAgent = "claude" | "codex" | "copilot" | "opencode" | "custom";
export type SetupRuntime = "claude-code" | "codex";
export type RuntimeSetupState = { show_on_launch: boolean; completed: boolean; default_agent?: SetupAgent; default_command?: string };
export type RuntimeSetupStatus = {
  protocol: 1; type: "status"; runtime: SetupRuntime; installed: boolean; version: string | null;
  probe: "missing" | "ok" | "unrecognized" | "timeout" | "failed";
  authentication: "not_checked" | "signed_in" | "signed_out" | "unknown";
  credential_method: "unknown" | "account" | "api_key"; environment_api_key_configured: boolean;
  reason?: "auth_status_unavailable";
};
export type RuntimeSetupEvent = { job_id: string; request_id: string; protocol: 1 } & (
  { type: "ready"; runtime: SetupRuntime; action: "install" | "login" } |
  { type: "output"; data: string } |
  { type: "exited"; exit_code: number; cancelled: false } |
  { type: "error"; reason: string }
);
type SetupOperations = {
  state: [Record<string, never>, RuntimeSetupState];
  status: [{ runtime: SetupRuntime; request_id: string; selection_handle?: string }, RuntimeSetupStatus];
  "choose-executable": [{ runtime: SetupRuntime }, { selection_handle: string; display_name: string } | { cancelled: true }];
  start: [{ runtime: SetupRuntime; action: "install" | "login"; request_id: string; selection_handle?: string }, { job_id: string; request_id: string }];
  input: [{ job_id: string; data: string }, unknown];
  resize: [{ job_id: string; cols: number; rows: number }, unknown];
  cancel: [{ job_id: string } | { request_id: string }, unknown];
  complete: [{ skipped: boolean; agent?: SetupAgent; command?: string; selection_handle?: string }, RuntimeSetupState];
};

export function canSetUpAgents(): boolean {
  const native = window.__rubbertermDesktop;
  return native?.currentTarget === "local" && native.canSetUpAgents === true && !!window.webkit?.messageHandlers?.runtimeSetup;
}

export async function runtimeSetupRequest<K extends keyof SetupOperations>(operation: K, params: SetupOperations[K][0]): Promise<SetupOperations[K][1]> {
  const bridge = window.webkit?.messageHandlers?.runtimeSetup;
  if (!canSetUpAgents() || !bridge) throw new Error("Agent setup is available in the local DuckTerm Mac app.");
  return await bridge.postMessage({ operation, params }) as SetupOperations[K][1];
}

export function rememberRuntimeSetup(state: RuntimeSetupState): void {
  if (window.__rubbertermDesktop) window.__rubbertermDesktop.agentSetup = state;
}

// null until the edition loads. The edition decides whether other computers
// exist for this dashboard; every host list, launch target and remote poll
// reads them through desktop().
let remoteWorkspaces: boolean | null = null;
let collaboration = false;

// The bridge advertises native support, but the active server may not provide
// collaboration. Core folder actions must then use their ordinary HTTP routes.
export function allowCollaboration(allowed: boolean): void {
  collaboration = allowed;
}

export function allowRemoteWorkspaces(allowed: boolean | null): void {
  if (allowed === remoteWorkspaces) return;
  remoteWorkspaces = allowed;
  window.dispatchEvent(new Event("desktop-targets-changed"));
}

// A computer the Mac app knows but desktop() hides: "pending" until the edition loads.
export function hiddenHost(host: string): "pending" | "hidden" | null {
  if (host === "local" || remoteWorkspaces === true || !window.__rubbertermDesktop?.targets.some(t => t.id === host)) return null;
  return remoteWorkspaces === null ? "pending" : "hidden";
}

export function desktop(): Desktop | undefined {
  const bridge = window.__rubbertermDesktop;
  if (!bridge) return bridge;
  const available = { ...bridge, canCollaborate: bridge.canCollaborate === true && collaboration };
  if (remoteWorkspaces === true) return available;
  return { ...available, currentTarget: "local", launchTarget: undefined, targets: bridge.targets.filter(t => t.id === "local") };
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

// Touch ID approval lives in the Mac app: the Secure Enclave key signs one
// server challenge per click. Browsers have no bridge and cannot approve.
export function approvalSigner(): { publicKey: () => Promise<string>; sign: (payload: string) => Promise<string> } | null {
  const bridge = window.webkit?.messageHandlers?.approval;
  if (!desktop()?.canApprove || !bridge) return null;
  return {
    publicKey: async () => await bridge.postMessage({ operation: "public-key" }) as string,
    sign: async (payload: string) => await bridge.postMessage({ operation: "sign", payload }) as string,
  };
}
