import { authHeaders } from "./api";
import { sessionFetch, splitSessionRef } from "./hostTransport";
import type { CheckpointClock } from "./CheckpointAge";
import type { CheckpointRecord } from "./api";

export interface MemoryEvidence { source: string; record: string; title: string; kind: string; role: string; retained: boolean }
export interface MemoryClaim { id?: string; text: string; kind?: string; state?: string; source?: string; record: string; evidence: MemoryEvidence[] }
export interface MemoryGroup { field: string; claims: MemoryClaim[] }
export interface SavedMemory {
  state: "available" | "unavailable" | "empty"; current_revision_id: string | null;
  checkpoint_clock: CheckpointClock;
  latest_checkpoint?: { id: string; created_at: number; label: string; summary_update: CheckpointRecord["summary_update"] | null } | null;
  revision: null | { id: string; source: string; created_at: number; summary: string | null; overview: string | null; is_current: boolean; verified_at_save: boolean; groups: MemoryGroup[]; withheld_claims: number; coverage_at_save: { summarized_records?: number; available_records?: number; remaining_records?: number } };
  revisions: { id: string; created_at: number }[]; revisions_truncated: boolean;
  memory?: { state: "available" | "unavailable"; groups: MemoryGroup[]; narratives: { revision: string; text: string }[]; span_count?: number; reason?: string; older_narratives_for_retrieval?: number };
}
export interface MemoryRead { text: string; next_offset: number | null; source: string; title: string }
export async function memoryGet<T>(key: string, action = "", query: Record<string, string> = {}): Promise<T> {
  const local = splitSessionRef(key).key;
  const suffix = new URLSearchParams(query).toString();
  const response = await sessionFetch(key, `/sessions/${encodeURIComponent(local)}/memory${action ? `/${action}` : ""}${suffix ? `?${suffix}` : ""}`, { headers: authHeaders(), cache: "no-store" });
  const value = await response.json();
  if (!response.ok) throw new Error(value.error ?? "Saved memory is unavailable");
  return value as T;
}
