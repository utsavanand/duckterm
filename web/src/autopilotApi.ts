import { authHeaders } from "./api";

// The /api/autopilot routes (src/duckterm/autopilot/api.py), behind the
// `autopilot` development flag.

export type TriggerType = "github-issue" | "github-pr" | "github-ci" | "jira-issue" | "slack-message" | "schedule";

export interface AutopilotFlow {
  id: string;
  kit: string;
  name: string;
  description: string;
  triggers: TriggerType[];
  gated: boolean;
}

export interface AutopilotEntry {
  id: string;
  on: boolean;
  created_at: number;
  flow: string;
  name: string;
  workspace: string;
  repo: string;
  triggers: Partial<Record<TriggerType, { labels: string[] }>>;
  approvals: { spec_on_visible: boolean; keep_merge_approval: boolean };
  budget_m: number;
  max_runs: number;
  active: number;
  watch: { checked_at?: number; error?: string };
}

export interface AutopilotRun {
  id: string;
  autopilot: string;
  autopilot_name: string;
  workspace: string;
  flow: string;
  repo: string;
  trigger: TriggerType;
  item: { number?: number; title?: string; url?: string; started_by?: string | null };
  branch: string;
  state: "starting" | "running" | "waiting_gate" | "succeeded" | "failed" | "cancelled" | "interrupted";
  started_at: number;
  ended_at?: number;
  tokens?: number;
  error?: string;
  failure?: unknown;
  current?: string;
  steps?: { step: string; attempt: number; exit?: number | null; choice?: string | null }[];
}

export interface AutopilotSummary {
  available: boolean;
  active: number;
  waiting_gate: number;
  done_today: number;
  failed_today: number;
}

export interface AutopilotOverview {
  flows: AutopilotFlow[];
  triggers: Record<TriggerType, { needs: string | null; available: boolean }>;
  autopilots: AutopilotEntry[];
  runs: AutopilotRun[];
  summary: AutopilotSummary;
  problems: string[];
}

export interface AutopilotDraft {
  flow: string;
  name: string;
  workspace: string;
  repo: string;
  triggers: Partial<Record<TriggerType, { labels: string[] }>>;
  approvals: { spec_on_visible: boolean; keep_merge_approval: boolean };
  budget_m: number;
  max_runs: number;
}

async function call<T>(method: string, path: string, body?: unknown): Promise<T> {
  const res = await fetch(path, {
    method,
    cache: "no-store",
    headers: authHeaders(body === undefined ? undefined : { "Content-Type": "application/json" }),
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const data: unknown = await res.json().catch(() => ({}));
  if (!res.ok) {
    const error = (data as { error?: unknown }).error;
    throw new Error(typeof error === "string" && error ? error : `${res.status} ${res.statusText}`);
  }
  return data as T;
}

export const autopilotApi = {
  // A workspace includes its subfolders.
  overview: (workspace?: string) =>
    call<AutopilotOverview>("GET", workspace === undefined ? "/api/autopilot" : `/api/autopilot?workspace=${encodeURIComponent(workspace)}`),
  summary: () => call<AutopilotSummary>("GET", "/api/autopilot/summary"),
  repositories: () => call<string[]>("GET", "/api/autopilot/repositories"),
  create: (draft: AutopilotDraft) => call<AutopilotEntry>("POST", "/api/autopilot/autopilots", draft),
  update: (id: string, patch: Partial<AutopilotDraft> & { on?: boolean }) =>
    call<AutopilotEntry>("PATCH", `/api/autopilot/autopilots/${encodeURIComponent(id)}`, patch),
  remove: (id: string) => call<{ ok: boolean }>("DELETE", `/api/autopilot/autopilots/${encodeURIComponent(id)}`),
  runOnce: (id: string, issue: number) =>
    call<{ run_id: string }>("POST", `/api/autopilot/autopilots/${encodeURIComponent(id)}/run`, { issue }),
};
