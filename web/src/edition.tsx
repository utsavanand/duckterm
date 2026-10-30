import { createContext, ReactNode, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import { api, EditionDescription } from "./api";
import { allowCollaboration, allowRemoteWorkspaces } from "./desktop";

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
const EditionContext = createContext<{ state: EditionState; refresh: () => void }>({ state: { status: "loading" }, refresh: () => undefined });

function parse(value: unknown): EditionDescription {
  const description = value as Partial<EditionDescription> | null;
  if (!["lite", "code", "full"].includes(description?.id as string) || !Array.isArray(description?.features)
    || !description.features.every(feature => typeof feature === "string")) throw new Error("Malformed edition description");
  return { id: description.id!, features: description.features };
}

const same = (a: EditionDescription, b: EditionDescription) => a.id === b.id && a.features.join() === b.features.join();

export function EditionProvider({ children, onError }: { children: ReactNode; onError?: (message: string) => void }) {
  const [state, setState] = useState<EditionState>({ status: "loading" });
  const refresh = useRef(() => {});
  useEffect(() => {
    let stopped = false, inflight = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    // A server restarted in another edition is noticed on reconnect or focus.
    const load = () => {
      if (stopped || inflight) return;
      inflight = true; clearTimeout(timer);
      api.edition().then(parse).then(description => {
        if (!stopped) setState(previous => previous.status === "ready" && same(previous.description, description) ? previous : { status: "ready", description });
      }, (error: Error) => {
        if (stopped) return;
        // The backend stays authoritative: an unknown edition keeps only the
        // core until a description arrives, rather than assuming Full or
        // keeping a list the server may no longer have.
        setState(previous => previous.status === "error" && previous.error === error.message ? previous : { status: "error", error: error.message });
        timer = setTimeout(load, RETRY_MS);
      }).finally(() => { inflight = false; });
    };
    refresh.current = load;
    load();
    window.addEventListener("focus", load);
    return () => { stopped = true; clearTimeout(timer); window.removeEventListener("focus", load); };
  }, []);
  const reload = useCallback(() => refresh.current(), []);
  const remote = state.status === "ready" ? state.description.features.includes("remote_workspaces") : null;
  useEffect(() => allowRemoteWorkspaces(remote), [remote]);
  const collaboration = state.status === "ready" && state.description.features.includes("inbox");
  useEffect(() => {
    allowCollaboration(collaboration);
    return () => allowCollaboration(false);
  }, [collaboration]);
  const error = state.status === "error" ? state.error : null;
  useEffect(() => { if (error) onError?.(error); }, [error, onError]);
  const value = useMemo(() => ({ state, refresh: reload }), [state, reload]);
  return <EditionContext.Provider value={value}>{children}</EditionContext.Provider>;
}

export function useEdition() {
  const { state, refresh } = useContext(EditionContext);
  const has = useCallback((feature: Feature) =>
    state.status === "ready" && state.description.features.includes(feature), [state]);
  return { state, has, refresh, id: state.status === "ready" ? state.description.id : null };
}

export function useFeature(feature: Feature): boolean {
  return useEdition().has(feature);
}

// Test and preview fixtures: a fixed, already-loaded description.
export function StaticEdition({ id = "full", features, children }: { id?: EditionDescription["id"]; features: string[]; children: ReactNode }) {
  const value = useMemo(() => ({ state: { status: "ready", description: { id, features } } as EditionState, refresh: () => undefined }), [id, features]);
  return <EditionContext.Provider value={value}>{children}</EditionContext.Provider>;
}
