import { createContext, ReactNode, useCallback, useContext, useEffect, useState } from "react";
import { api, EditionDescription } from "./api";
import { allowRemoteWorkspaces } from "./desktop";

// Features outside the Lite core. Core surfaces (folders, terminal, shell,
// messages, forks, session status) are never gated here.
export type Feature =
  | "explorer" | "file_editing" | "git_changes" | "inbox" | "folder_chat" | "checkpoints"
  | "summaries" | "context" | "handoff" | "connectors" | "oracle" | "remote_workspaces"
  | "artifacts" | "pr_review";

export type EditionState =
  | { status: "loading" }
  | { status: "error"; error: string }
  | { status: "ready"; description: EditionDescription };

const RETRY_MS = 5000;

// Without a provider nothing advanced is available, so a component rendered
// outside the dashboard can never start an advanced request by default.
const EditionContext = createContext<EditionState>({ status: "loading" });

export function EditionProvider({ children, onError }: { children: ReactNode; onError?: (message: string) => void }) {
  const [state, setState] = useState<EditionState>({ status: "loading" });
  useEffect(() => {
    let stopped = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const load = () => {
      api.edition().then(description => {
        if (!stopped) setState({ status: "ready", description });
      }, (error: Error) => {
        if (stopped) return;
        // The backend stays authoritative: an unknown edition keeps only the
        // core until a description arrives, rather than assuming Full.
        setState(previous => previous.status === "ready" || (previous.status === "error" && previous.error === error.message)
          ? previous : { status: "error", error: error.message });
        timer = setTimeout(load, RETRY_MS);
      });
    };
    load();
    return () => { stopped = true; clearTimeout(timer); };
  }, []);
  const remote = state.status === "ready" ? state.description.features.includes("remote_workspaces") : null;
  useEffect(() => allowRemoteWorkspaces(remote), [remote]);
  const error = state.status === "error" ? state.error : null;
  useEffect(() => { if (error) onError?.(error); }, [error, onError]);
  return <EditionContext.Provider value={state}>{children}</EditionContext.Provider>;
}

export function useEdition() {
  const state = useContext(EditionContext);
  const has = useCallback((feature: Feature) =>
    state.status === "ready" && state.description.features.includes(feature), [state]);
  return { state, has, id: state.status === "ready" ? state.description.id : null };
}

export function useFeature(feature: Feature): boolean {
  return useEdition().has(feature);
}

// Test and preview fixtures: a fixed, already-loaded description.
export function StaticEdition({ id = "full", features, children }: { id?: EditionDescription["id"]; features: string[]; children: ReactNode }) {
  return <EditionContext.Provider value={{ status: "ready", description: { id, features } }}>{children}</EditionContext.Provider>;
}
