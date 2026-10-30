import type { Bucket, ReviewCard } from "../api";

export interface Claim { claim: string; in_diff: "true" | "false" | "partial"; evidence: string[]; evidence_missing?: string[]; note?: string }
export interface IntentData { summary: string; claims: Claim[]; unclaimed: { path: string; what: string }[]; diagram: string; verdict: string }
export interface Stop { path: string; hunk: number; title: string; why: string; bucket: Bucket }
export interface WalkthroughData { stops: Stop[]; minutes: Record<Bucket, number>; verdict: string }
export interface RiskFile { path: string; bucket: Bucket; rules: { bucket: Bucket; id: string; reason: string }[]; lines: number; reverts: number; hotspot: string | null }
export interface RiskData { files: RiskFile[]; counts: Record<Bucket, number>; reasons: { path: string; reason: string }[]; verdict: string }
export interface BehaviourRow { situation: string; before: string; after: string; who_notices: string; regression_risk: "low" | "medium" | "high"; evidence: string[]; evidence_missing?: string[] }
export interface BehaviourData { rows: BehaviourRow[]; verdict: string }
export interface ReleaseCheck { name: string; hits: string[]; status: "pass" | "fail" | "conditional" | "info" | "unknown"; note: string }
export interface ReleaseData { checks: ReleaseCheck[]; low_risk: "true" | "false" | "conditional"; condition: string; verdict: string }
export interface Question { kind: "unverified" | "confirm" | "cleanup" | "manual"; text: string; paths: string[] }
export interface QuestionsData { items: Question[]; verdict: string }

export interface SlopFinding { path: string; line: number; kind: string; text: string }
export interface TestsData {
  coverage: { path: string; tests: string[]; covered: "yes" | "partial" | "no"; note: string }[]; gaps: string[];
  suggested: { name: string; path: string; body: string }[]; slop: SlopFinding[]; existing_tests: Record<string, string[]>;
  report: { files: Record<string, number>; source: string } | null; gate: { at: number; age_minutes: number; passed: boolean; summary: string | null } | null;
  model_status: "ready" | "omitted"; model_reason?: string; verdict: string;
}
export interface GuardrailCheck { rule: string; status: "pass" | "finding" | "n/a"; note: string; items: { path?: string; line?: number; sha?: string; kind?: string; text: string }[] }
export interface GuardrailsData { checks: GuardrailCheck[]; findings: { rule: string; status: "pass" | "finding" | "n/a"; location: string; note: string }[]; rule_files: string[]; model_status: "ready" | "omitted"; model_reason?: string; verdict: string }

export interface BlastSymbol { symbol: string; kind: string; path: string; line: number; change: "added" | "changed" | "removed"; callers_in_diff: string[]; callers_outside: string[]; outside_count: number; tested: boolean }
export interface BlastData { symbols: BlastSymbol[]; diagram: string; index: { base_files: number; head_files: number }; limits: string; consequence: string; risks: { symbol: string; note: string }[]; model_status: "ready" | "omitted"; model_reason?: string; verdict: string }
export interface TraceEvent { turn: number | null; kind: string; text: string; ts: number | null }
export interface AuthorData {
  events: TraceEvent[]; turns: number; commits: number; pushes: number; verification_runs: TraceEvent[]; abandoned_commands: TraceEvent[];
  owner_decisions: { text: string; ts: number | null }[]; session: { key: string; name: string; runtime: string | null };
  trajectory?: string; abandoned?: string[]; decisions?: string[]; verification?: string; model_status: "ready" | "omitted"; model_reason?: string; verdict: string;
}

export type Severity = "ok" | "warn" | "danger" | "neutral" | "muted";

export function verdict(card: ReviewCard): string {
  if (card.status === "ready") return String(card.data?.verdict ?? "Ready");
  if (card.status === "pending") return card.with?.length ? `Writing with ${card.with.join(", ")}…` : "Writing…";
  if (card.status === "stale") return "Head moved";
  if (card.status === "later") return "Later";
  return card.reason ? `Omitted: ${card.reason}` : "Omitted";
}

export function severity(card: ReviewCard): Severity {
  if (card.status !== "ready") return card.status === "later" ? "muted" : "neutral";
  const data = card.data ?? {};
  switch (card.id) {
    case "intent": {
      const claims = (data.claims as Claim[] | undefined) ?? [];
      return claims.some(c => c.in_diff === "false") ? "danger" : claims.some(c => c.in_diff === "partial") ? "warn" : "ok";
    }
    case "risk": return ((data.counts as Record<string, number> | undefined)?.high ?? 0) > 0 ? "danger" : "ok";
    case "behaviour": {
      const rows = (data.rows as BehaviourRow[] | undefined) ?? [];
      return rows.some(r => r.regression_risk === "high") ? "danger" : rows.some(r => r.regression_risk === "medium") ? "warn" : "ok";
    }
    case "release": return data.low_risk === "true" ? "ok" : data.low_risk === "conditional" ? "warn" : "danger";
    case "tests": {
      const tests = data as unknown as TestsData;
      return tests.slop.length > 0 || tests.coverage.some(row => row.covered === "no") ? "danger" : tests.coverage.some(row => row.covered === "partial") || tests.gaps.length > 0 ? "warn" : "ok";
    }
    case "blast": {
      const blast = data as unknown as BlastData;
      return blast.symbols.some(s => s.outside_count > 0 && !s.tested) ? "danger" : blast.symbols.some(s => s.outside_count > 0) ? "warn" : "ok";
    }
    case "author": {
      const author = data as unknown as AuthorData;
      return author.abandoned_commands.length > 0 ? "warn" : "neutral";
    }
    case "guardrails": {
      const guard = data as unknown as GuardrailsData;
      return guard.checks.some(c => c.status === "finding") || guard.findings.some(f => f.status === "finding") ? "danger" : "ok";
    }
    case "questions": return ((data.items as Question[] | undefined)?.length ?? 0) > 0 ? "warn" : "ok";
    default: return "neutral";
  }
}

export const KIND_LABELS: Record<Question["kind"], string> = { unverified: "Not shown in the diff", confirm: "Please confirm", cleanup: "Before merge", manual: "Needs a hand test" };
