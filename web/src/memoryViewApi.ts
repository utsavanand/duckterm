import { authHeaders } from "./api";
import { sessionFetch, splitSessionRef } from "./hostTransport";
import type { CheckpointClock } from "./CheckpointAge";
import type { CheckpointAttemptCoverage, CheckpointRecord } from "./api";

export interface MemoryEvidence { source: string; record: string; title: string; kind: string; role: string; retained: boolean }
export interface MemoryClaim { id?: string; text: string; kind?: string; state?: string; source?: string; record: string; evidence: MemoryEvidence[] }
export interface MemoryGroup { field: string; claims: MemoryClaim[] }
export interface MemoryPagination {
  snapshot: string; total: number; offset: number; returned: number; next_cursor: string | null;
  total_claims?: number; returned_claims?: number;
}
export interface SavedRevision {
  id: string; source: string; created_at: number; summary: string | null; overview: string | null;
  is_current: boolean; verified_at_save: boolean; groups: MemoryGroup[]; withheld_claims: number;
  coverage_at_save: { summarized_records?: number | null; available_records?: number | null; remaining_records?: number | null };
}
export interface CheckpointMemory {
  scope: "checkpoint"; session_key: string;
  checkpoint: {
    id: string; created_at: number; label: string;
    summary_update: CheckpointRecord["summary_update"] | null;
    attempt_coverage: null | Omit<CheckpointAttemptCoverage, "state" | "reference_only_records"> & {
      state: "complete" | "incomplete"; reference_only_records?: number;
    };
    batches: { revision_id: string; state: "available" | "unavailable" | "empty"; revision: SavedRevision | null }[];
    pagination: MemoryPagination; batch_coverage_known: boolean; linked_revision_id?: string | null;
    combined_summary: null; combined_summary_reason: "independent_batch_summaries";
  };
}
export interface SavedMemory {
  state: "available" | "unavailable" | "empty"; current_revision_id: string | null;
  checkpoint_clock: CheckpointClock;
  latest_checkpoint?: { id: string; created_at: number; label: string; summary_update: CheckpointRecord["summary_update"] | null } | null;
  revision: SavedRevision | null;
  revisions: { id: string; created_at: number }[]; revisions_truncated: boolean;
  revisions_pagination?: MemoryPagination;
  memory?: { state: "available" | "unavailable"; groups: MemoryGroup[]; narratives: { revision: string; text: string }[]; span_count?: number; reason?: string; older_narratives_for_retrieval?: number; pagination?: MemoryPagination };
}
export interface MemoryRead { text: string; next_offset: number | null; source: string; title: string }
export class MemoryReadError extends Error {
  constructor(message: string, readonly status: number) { super(message); this.name = "MemoryReadError"; }
}
function validateViewQuery(query: Record<string, string>) {
  const allowed = new Set(["revision", "checkpoint", "claims_cursor", "claims_limit", "revisions_cursor", "revisions_limit"]);
  for (const [key, value] of Object.entries(query)) {
    if (!allowed.has(key) || typeof value !== "string" || !value
      || (key.endsWith("_limit") && (!/^\d+$/.test(value) || Number(value) < 1 || Number(value) > 100))) {
      throw new MemoryReadError("Invalid saved-memory query", 400);
    }
  }
  if (("checkpoint" in query && ["revision", "claims_cursor", "claims_limit"].some(key => key in query))
    || ("revision" in query && "claims_cursor" in query)) {
    throw new MemoryReadError("Cannot combine saved-memory scopes", 400);
  }
}
export async function memoryGet<T>(key: string, action = "", query: Record<string, string> = {}): Promise<T> {
  if (!action) validateViewQuery(query);
  const local = splitSessionRef(key).key;
  const suffix = new URLSearchParams(query).toString();
  const response = await sessionFetch(key, `/sessions/${encodeURIComponent(local)}/memory${action ? `/${action}` : ""}${suffix ? `?${suffix}` : ""}`, { headers: authHeaders(), cache: "no-store" });
  const value = await response.json().catch(() => null);
  if (!response.ok) throw new MemoryReadError(typeof value?.error === "string" ? value.error : "Saved memory is unavailable", response.status);
  if (!value || typeof value !== "object") throw new MemoryReadError("Invalid saved-memory response", response.status);
  return value as T;
}
