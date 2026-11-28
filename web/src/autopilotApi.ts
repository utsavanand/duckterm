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
  issue_repo?: string;
  triggers: Partial<Record<TriggerType, { labels: string[] }>>;
  approvals: { spec_on_visible: boolean; keep_merge_approval: boolean };
  budget_m: number;
  max_runs: number;
  test_command?: string;
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
  state: "starting" | "running" | "waiting_gate" | "succeeded" | "rejected" | "needs_info" | "declined" | "failed" | "cancelled" | "interrupted";
  // Why a run ended at triage (needs_info, declined).
  outcome?: string;
  started_at: number;
  ended_at?: number;
  tokens?: number;
  error?: string;
  failure?: unknown;
  current?: string;
  steps?: { step: string; attempt: number; exit?: number | null; choice?: string | null }[];
  pushed?: boolean;
  pr?: { number: number; url: string; base: string };
  pr_error?: string;
  pr_skipped?: string;
  // Set while state is waiting_gate. `shown` is the step output the gate
  // asks about (the spec), as text written by the model; `why` is the policy
  // step's reason for asking.
  gate?: { step: string; question: string; choices: string[]; shown: string; why: string; asked_at: number } | null;
  answers?: { step: string; answer: string; note?: string | null; by: string; at: number }[];
  // What the spec step judged the fix to touch; the policy reads it.
  classification?: Classification | null;
}

export interface Classification {
  surfaces?: string[];
  user_visible?: boolean;
  regression?: { level?: string; features?: string[] };
  security?: { level?: string; areas?: string[] };
  ai?: string;
  data?: string;
  size?: string;
}

// One step attempt from the kit runner's receipt. Times are Unix seconds.
export interface AutopilotAttempt {
  step: string;
  attempt: number;
  kind: string | null;
  status: string | null;
  exit: number | null;
  choice: string | null;
  reason: string | null;
  harness?: string | null;
  started_at: number | null;
  finished_at: number | null;
}

export interface AutopilotGate {
  step: string;
  question: string | null;
  choices: string[];
  asked_at: number;
  response: { choice?: string } | null;
}

export interface AutopilotChanges {
  files: { path: string; added: number; removed: number; binary: boolean }[];
  patch: string;
  cut: boolean;
}

export interface AutopilotRunDetail extends AutopilotRun {
  attempts: AutopilotAttempt[];
  gates: AutopilotGate[];
  loops: Record<string, number>;
  issue_body: string;
  spec: string | null;
  changes: AutopilotChanges | null;
  sandbox?: string;
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
  // "" when issues come from repo itself
  issue_repo: string;
  triggers: Partial<Record<TriggerType, { labels: string[] }>>;
  approvals: { spec_on_visible: boolean; keep_merge_approval: boolean };
  budget_m: number;
  max_runs: number;
  test_command: string;
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
  // backlog: what happens to open issues an edit's new labels match ("new" by default).
  update: (id: string, patch: Partial<AutopilotDraft> & { on?: boolean; backlog?: "run" | "new" }) =>
    call<AutopilotEntry>("PATCH", `/api/autopilot/autopilots/${encodeURIComponent(id)}`, patch),
  // Open issues an edit's labels and issue repository would match that have not run.
  backlog: (id: string, patch: Partial<AutopilotDraft>) =>
    call<{ issues: number[] }>("POST", `/api/autopilot/autopilots/${encodeURIComponent(id)}/backlog`, patch),
  remove: (id: string) => call<{ ok: boolean }>("DELETE", `/api/autopilot/autopilots/${encodeURIComponent(id)}`),
  run: (id: string) => call<AutopilotRunDetail>("GET", `/api/autopilot/runs/${encodeURIComponent(id)}`),
  runOnce: (id: string, issue: number) =>
    call<{ run_id: string }>("POST", `/api/autopilot/autopilots/${encodeURIComponent(id)}/run`, { issue }),
  answer: (runId: string, step: string, answer: string, note?: string) =>
    call<{ ok: boolean }>("POST", `/api/autopilot/runs/${encodeURIComponent(runId)}/gate`, { step, answer, note }),
};
