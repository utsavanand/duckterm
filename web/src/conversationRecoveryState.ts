import type { ConversationBinding, SessionView } from "./types";

export interface ConversationIdentity {
  status: "missing" | "pending" | "recorded" | "contested";
  source: "none" | "assigned" | "observed" | "adopted";
  transcript: "unknown" | "present" | "missing" | "unavailable";
  reason?: string;
  hooks: { status: "missing" | "configured" | "unknown"; canInstall: boolean };
  canAdopt: boolean;
  canResume: boolean;
  canDetach?: boolean;
  revision?: string;
}

export interface ConversationCandidate {
  handle: string;
  label: string;
  firstPrompt: string | null;
  lastPrompt: string | null;
  modifiedAt: number;
  promptCount?: number;
  available: boolean;
  reason?: string;
}
export interface ConversationCandidates {
  revision: string;
  candidates: ConversationCandidate[];
  hasMore?: boolean;
}

/** All methods are bound to one session and computer by the API adapter. */
export interface ConversationRecoveryService {
  identity: () => Promise<ConversationIdentity>;
  installHooks: () => Promise<ConversationIdentity>;
  candidates: () => Promise<ConversationCandidates>;
  adopt: (handle: string, revision: string) => Promise<ConversationIdentity>;
  detach: (revision: string) => Promise<ConversationIdentity>;
}

export function identityPresentation(value: ConversationIdentity): { title: string; detail: string } {
  if (value.status === "contested") return { title: "Resume blocked", detail: "The observed conversation ID differs from the one recorded for this session. DuckTerm will not replace either identity automatically." };
  if (value.status === "missing" && value.hooks.status === "configured") return { title: "Awaiting conversation ID", detail: "Hooks are configured, but no conversation ID has been captured. Installing hooks does not recover a previously unrecorded conversation." };
  if (value.status === "missing") return { title: "Resume unavailable", detail: value.hooks.status === "missing"
    ? "Conversation ID not recorded. Hooks are not configured on this computer."
    : "This session has no recorded conversation ID." };
  if (value.status === "pending") return { title: "Resume not ready yet", detail: "Waiting for this session to report its conversation ID. Installing hooks alone does not confirm that the ID was recorded." };
  if (value.transcript !== "present") return { title: "Conversation identity recorded", detail: value.transcript === "missing"
    ? "The conversation ID is recorded, but its transcript could not be found. Resume remains unavailable."
    : "The conversation ID is recorded. Transcript availability has not been verified; it must be checked before resuming." };
  return { title: value.source === "adopted" ? "Conversation chosen by you" : "Conversation identity recorded", detail: value.canResume
    ? "The recorded conversation and its transcript are available. Resume opens that conversation."
    : "The conversation identity and transcript are recorded. Resume remains subject to this session’s current state." };
}

/** Older servers omit the capability: preserve their behavior without inventing facts. */
export function launchIdentity(binding: ConversationBinding): ConversationIdentity {
  return { ...binding, transcript: "unknown", hooks: { status: "unknown", canInstall: false }, canAdopt: false, canResume: binding.status === "recorded" };
}
export function identityBlocksResume(session: SessionView): boolean {
  return ["claude-code", "codex", "copilot"].includes(session.runtime ?? "") && !!session.conversationIdentity && session.conversationIdentity.status !== "recorded";
}
