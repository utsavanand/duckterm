// Preparation and explicit switching remain separate. Never retry mutations here.
import { authHeaders, type RestartOptions } from "./api";
import { routedFetch, splitSessionRef } from "./hostTransport";
import { type PreparationResult, type SwitchSelection, type SwitchSubmission, type OperationResult } from "./memorySwitchState";

type ObjectValue = Record<string, unknown>;
export interface MemoryGap { source_id?: string; kind: string; reason: string; blocking: boolean }
export interface MaintainedHandoff {
  method: "maintained";
  summary_revision_id: string | null; summary_generated_at: number | null;
  summary_state: "current" | "partial" | "legacy" | "unavailable";
  available_records: number; summarized_records: number; included_records: number; omitted_records: number;
}
export interface MemoryCoverage {
  state: "complete" | "partial" | "unknown";
  available_text: "processed" | "partial" | "not_processed";
  retrieval: "available" | "partial" | "unavailable";
  retention: "native_conditional" | "retained_snapshot" | "mixed" | "unknown";
  source_count: number; covered_source_count: number; gap_count: number;
  gaps: MemoryGap[]; has_more: boolean; details_cursor: string | null; handoff?: MaintainedHandoff;
}
export interface MemoryPreparation {
  preparationId: string; sequence: number; result: PreparationResult;
  coverage: MemoryCoverage; overview?: string; resolvedModel?: string | null;
  revisionId?: string | null; preparedAt?: number; retryable?: boolean;
}
export interface MemoryDetails {
  preparationId: string; snapshotId: string;
  brief: { text: string; utf8_bytes: number; budget_bytes: number };
  sources: { source_id: string; source_version: string; read_handle: string }[];
  gaps: MemoryGap[]; nextCursor: string | null;
}
export class MemoryTransportError extends Error {
  constructor(message: string, readonly status: number, readonly code: string,
    readonly processState: OperationResult["processState"] = "unknown", readonly retryable = false) {
    super(message); this.name = "MemoryTransportError";
  }
}
function invalid(): never { throw new MemoryTransportError("Invalid memory response", 0, "invalid_response"); }
function obj(value: unknown): ObjectValue {
  return value !== null && typeof value === "object" && !Array.isArray(value) ? value as ObjectValue : invalid();
}
function str(value: unknown, max = 4096): string {
  return typeof value === "string" && value.length <= max ? value : invalid();
}
function nonempty(value: unknown, max = 4096): string { const s = str(value, max); return s ? s : invalid(); }
function num(value: unknown): number { return typeof value === "number" && Number.isSafeInteger(value) && value >= 0 ? value : invalid(); }
function bool(value: unknown): boolean { return typeof value === "boolean" ? value : invalid(); }
function choice<T extends string>(value: unknown, values: readonly T[]): T {
  return typeof value === "string" && values.includes(value as T) ? value as T : invalid();
}
function list<T>(value: unknown, parse: (value: unknown) => T, max = 50): T[] {
  return Array.isArray(value) && value.length <= max ? value.map(parse) : invalid();
}
function cursor(value: unknown): string | null {
  return value === null ? null : /^\d{1,8}$/.test(str(value, 8)) ? value as string : invalid();
}
function identity(value: string): string {
  if (!/^[A-Za-z0-9._:-]{1,128}$/.test(value)) throw new MemoryTransportError("Invalid request identity", 0, "invalid_request");
  return encodeURIComponent(value);
}
function gap(value: unknown): MemoryGap {
  const v = obj(value);
  return { ...(v.source_id === undefined ? {} : { source_id: nonempty(v.source_id) }),
    kind: nonempty(v.kind), reason: str(v.reason), blocking: bool(v.blocking) };
}
function handoff(value: unknown): MaintainedHandoff {
  const v = obj(value);
  const h: MaintainedHandoff = {
    method: choice(v.method, ["maintained"]),
    summary_revision_id: v.summary_revision_id === null ? null : nonempty(v.summary_revision_id),
    summary_generated_at: v.summary_generated_at === null ? null : num(v.summary_generated_at),
    summary_state: choice(v.summary_state, ["current", "partial", "legacy", "unavailable"]),
    available_records: num(v.available_records), summarized_records: num(v.summarized_records),
    included_records: num(v.included_records), omitted_records: num(v.omitted_records),
  };
  if (h.summarized_records + h.included_records + h.omitted_records !== h.available_records
    || (h.summary_revision_id === null) !== (h.summary_generated_at === null)
    || (h.summary_state === "unavailable") !== (h.summary_revision_id === null)
    || (h.summary_state === "current" && h.summarized_records !== h.available_records)
    || (["unavailable", "legacy"].includes(h.summary_state) && h.summarized_records !== 0)) invalid();
  return h;
}
function coverage(value: unknown): MemoryCoverage {
  const v = obj(value);
  const c: MemoryCoverage = {
    state: choice(v.state, ["complete", "partial", "unknown"]),
    available_text: choice(v.available_text, ["processed", "partial", "not_processed"]),
    retrieval: choice(v.retrieval, ["available", "partial", "unavailable"]),
    retention: choice(v.retention, ["native_conditional", "retained_snapshot", "mixed", "unknown"]),
    source_count: num(v.source_count), covered_source_count: num(v.covered_source_count), gap_count: num(v.gap_count),
    gaps: list(v.gaps, gap), has_more: bool(v.has_more), details_cursor: cursor(v.details_cursor),
    ...(v.handoff === undefined ? {} : { handoff: handoff(v.handoff) }),
  };
  if (c.covered_source_count > c.source_count || c.gaps.length > c.gap_count
    || c.has_more !== (c.gap_count > c.gaps.length)
    || c.has_more !== (c.details_cursor !== null)) invalid();
  if (c.handoff) {
    const h = c.handoff;
    const expected = h.summarized_records === h.available_records ? "processed" : h.summarized_records ? "partial" : "not_processed";
    if (c.available_text !== expected) invalid();
  }
  return c;
}
const processStates = ["source_running", "source_stopped", "target_running", "unknown"] as const;
function checkBinding(value: unknown, selection: SwitchSelection): void {
  const b = obj(value), t = obj(b.target), m = obj(t.model);
  if (b.session_key !== selection.sessionRef || b.source_generation !== selection.sourceGeneration
    || t.harness !== selection.harness || m.mode !== selection.model.mode
    || (selection.model.mode === "explicit" && m.id !== selection.model.id)) invalid();
}
function preparation(value: unknown, selection: SwitchSelection, expectedId?: string): MemoryPreparation {
  const v = obj(value);
  if (v.version !== 1) invalid();
  checkBinding(v.binding, selection);
  const preparationId = nonempty(v.preparation_id, 128);
  if (expectedId !== undefined && preparationId !== expectedId) invalid();
  const base = { preparationId, sequence: num(v.sequence), coverage: coverage(v.coverage) };
  // request_key on a shared job's GET can identify another lease. It is not
  // evidence that this response belongs to a different target or preparation.
  switch (v.state) {
    case "preparing": case "canceled": return { ...base, result: { phase: v.state } };
    case "incomplete_source": case "stale_source": case "failed": return { ...base,
      result: { phase: v.state, reason: str(v.reason) }, retryable: v.retryable === true };
    case "ready": {
      const p = obj(v.proof);
      const expiresAt = num(p.expires_at), preparedAt = num(p.prepared_at);
      const h = base.coverage.handoff;
      const revisionId = p.revision_id === null ? null : nonempty(p.revision_id);
      if (expiresAt <= preparedAt || base.coverage.retrieval !== "available"
        || base.coverage.retention !== "retained_snapshot" || base.coverage.state === "unknown"
        || base.coverage.gaps.some(g => g.blocking)
        || (h ? revisionId !== h.summary_revision_id : base.coverage.available_text !== "processed" || revisionId === null)) invalid();
      return { ...base, overview: str(p.overview, 32000), preparedAt,
        revisionId, resolvedModel: p.resolved_model === null ? null : nonempty(p.resolved_model, 200),
        result: { phase: "ready", proof: { selection: structuredClone(selection), preparationId,
          snapshotId: nonempty(p.snapshot_id), expiresAt } } };
    }
    default: return invalid();
  }
}
function operation(value: unknown, submission: SwitchSubmission, expectedId?: string): OperationResult {
  const v = obj(value);
  checkBinding(v.binding, submission.scope.selection);
  const id = nonempty(v.id, 128);
  if ((expectedId && id !== expectedId) || v.request_key !== submission.requestKey
    || v.preparation_id !== submission.proof.preparationId) invalid();
  const status = choice(v.status, ["queued", "restarting", "completed", "canceled", "failed"]);
  if (v.can_cancel !== (status === "queued")) invalid();
  return { id, requestKey: submission.requestKey, sequence: num(v.sequence),
    phase: status === "restarting" ? "switching" : status,
    processState: choice(v.process_state, processStates),
    ...(typeof v.code === "string" ? { code: str(v.code) } : {}),
    ...(status === "completed" ? { targetGeneration: nonempty(v.target_generation),
      configuredModel: v.configured_model === null || v.configured_model === "" ? null : nonempty(v.configured_model, 200) } : {}),
    ...(typeof v.error === "string" ? { reason: str(v.error) } : {}) };
}
export function memorySwitchAvailable(options: RestartOptions | null): boolean {
  return options?.memory_switch?.version === 1 && options.memory_switch.available === true
    && typeof options.current.conversation_generation === "string" && !!options.current.conversation_generation;
}
async function request(path: string, method = "GET", body?: unknown, signal?: AbortSignal): Promise<unknown> {
  // Network/abort errors are deliberately not turned into a failed operation.
  // The controller reconciles an ambiguous Switch by the original request key.
  const response = await routedFetch(path, { method, cache: "no-store", signal,
    headers: authHeaders(body === undefined ? undefined : { "Content-Type": "application/json" }),
    ...(body === undefined ? {} : { body: JSON.stringify(body) }) });
  const data: unknown = await response.json().catch(() => null);
  if (!response.ok) {
    const e = data && typeof data === "object" ? data as ObjectValue : {};
    throw new MemoryTransportError(typeof e.error === "string" ? e.error : `HTTP ${response.status}`,
      response.status, typeof e.code === "string" ? e.code : "http_error",
      processStates.includes(e.process_state as OperationResult["processState"]) ? e.process_state as OperationResult["processState"] : "unknown",
      e.retryable === true);
  }
  return data;
}
export function memorySwitchService(input: SwitchSelection) {
  const selection = structuredClone(input);
  const { host, key } = splitSessionRef(selection.sessionRef);
  // Native routes can be tested independently; the product's v1 switch remains local.
  function path(): string {
    if (host !== "local") throw new MemoryTransportError("Memory switching is available on This Mac only", 0, "unsupported");
    return `/sessions/${encodeURIComponent(key)}`;
  }
  function assertSubmission(s: SwitchSubmission) {
    checkBinding({ session_key: s.scope.selection.sessionRef, source_generation: s.scope.selection.sourceGeneration,
      target: { harness: s.scope.selection.harness, model: s.scope.selection.model } }, selection);
    checkBinding({ session_key: s.proof.selection.sessionRef, source_generation: s.proof.selection.sourceGeneration,
      target: { harness: s.proof.selection.harness, model: s.proof.selection.model } }, selection);
  }
  return {
    async prepare(requestKey: string) {
      identity(requestKey);
      const value = await request(`${path()}/restart-preparation`, "POST", { request_key: requestKey,
        binding: { session_key: key, source_generation: selection.sourceGeneration,
          target: { harness: selection.harness, model: selection.model } } });
      return preparation(value, selection);
    },
    async preparation(id: string, signal?: AbortSignal) {
      return preparation(await request(`${path()}/restart-preparation/${identity(id)}`, "GET", undefined, signal), selection, id);
    },
    async details(id: string, snapshotId: string, nextCursor?: string): Promise<MemoryDetails> {
      const query = new URLSearchParams({ detail: "full" });
      if (nextCursor !== undefined) { cursor(nextCursor); query.set("cursor", nextCursor); }
      const v = obj(await request(`${path()}/restart-preparation/${identity(id)}?${query}`));
      if (v.preparation_id !== id || v.snapshot_id !== snapshotId) invalid();
      const b = obj(v.brief);
      const brief = { text: str(b.text, 32000), utf8_bytes: num(b.utf8_bytes), budget_bytes: num(b.budget_bytes) };
      if (brief.budget_bytes > 32000 || brief.utf8_bytes > brief.budget_bytes
        || new TextEncoder().encode(brief.text).length !== brief.utf8_bytes) invalid();
      return { preparationId: id, snapshotId, brief, sources: list(v.sources, source => {
        const s = obj(source), source_id = nonempty(s.source_id), source_version = nonempty(s.source_version);
        const read_handle = nonempty(s.read_handle);
        if (read_handle !== `${source_id}:${source_version}`) invalid();
        return { source_id, source_version, read_handle };
      }), gaps: list(v.gaps, gap), nextCursor: cursor(v.next_cursor) };
    },
    async release(id: string, requestKey: string): Promise<void> {
      const value = obj(await request(`${path()}/restart-preparation/${identity(id)}?request_key=${identity(requestKey)}`, "DELETE"));
      if (value.released !== true) invalid();
    },
    async switch(submission: SwitchSubmission): Promise<OperationResult> {
      assertSubmission(submission); identity(submission.requestKey);
      return operation(await request(`${path()}/restart`, "POST", {
        harness: selection.harness, model: selection.model.mode === "explicit" ? selection.model.id : "",
        interrupt: submission.interrupt, request_key: submission.requestKey,
        memory: { version: 1, preparation_id: submission.proof.preparationId,
          snapshot_id: submission.proof.snapshotId, source_generation: selection.sourceGeneration },
      }), submission);
    },
    async operation(submission: SwitchSubmission, signal?: AbortSignal): Promise<OperationResult> {
      assertSubmission(submission);
      return operation(await request(`${path()}/restart?request_key=${identity(submission.requestKey)}`, "GET", undefined, signal), submission);
    },
    async cancel(submission: SwitchSubmission, operationId: string): Promise<OperationResult> {
      assertSubmission(submission);
      return operation(await request(`${path()}/restart?operation_id=${identity(operationId)}`, "DELETE"), submission, operationId);
    },
  };
}
