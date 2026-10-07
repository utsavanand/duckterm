// Pure UI state guards. Not wired into RestartControls until contract and visual
// review are complete. API responses must be validated by the future adapter.
export interface SwitchSelection {
  sessionRef: string; // Includes owning host, not merely the raw session ID.
  sourceGeneration: string;
  sourceHarness: string;
  harness: string;
  model: { mode: "default" } | { mode: "explicit"; id: string };
}
export interface PreparationScope { selection: SwitchSelection; controllerId: string; attempt: number }
export interface SwitchProof {
  preparationId: string;
  snapshotId: string;
  expiresAt: number;
  selection: SwitchSelection;
}
export type PreparationResult =
  | { phase: "preparing" }
  | { phase: "ready"; proof: SwitchProof }
  | { phase: "incomplete_source" | "stale_source" | "failed"; reason: string };
export type OperationResult = {
  phase: "queued" | "switching" | "completed" | "canceled" | "failed";
  id: string;
  requestKey: string;
  sequence: number;
  processState: "source_running" | "source_stopped" | "target_running" | "unknown";
  reason?: string;
};
export interface SwitchSubmission {
  scope: PreparationScope;
  requestKey: string;
  proof: SwitchProof;
  interrupt: boolean;
}
export interface MemorySwitchState {
  scope: PreparationScope;
  phase: "choosing" | PreparationResult["phase"] | OperationResult["phase"]
    | "submitting" | "submission_unknown";
  preparationSequence: number;
  interrupt: boolean;
  proof?: SwitchProof;
  submission?: SwitchSubmission;
  operation?: OperationResult;
  reason?: string;
}
export interface SwitchEligibility {
  now: number;
  draftClear: boolean;
  targetAvailable: boolean;
  memoryAvailable: boolean;
  pendingOperation: boolean;
  canInterrupt: boolean;
}
function sameSelection(a: SwitchSelection, b: SwitchSelection): boolean {
  return a.sessionRef === b.sessionRef && a.sourceGeneration === b.sourceGeneration
    && a.sourceHarness === b.sourceHarness && a.harness === b.harness
    && a.model.mode === b.model.mode
    && (a.model.mode !== "explicit" || (b.model.mode === "explicit" && a.model.id === b.model.id));
}
function sameScope(a: PreparationScope, b: PreparationScope): boolean {
  return a.controllerId === b.controllerId && a.attempt === b.attempt && sameSelection(a.selection, b.selection);
}
function copySelection(selection: SwitchSelection): SwitchSelection {
  return { ...selection, model: { ...selection.model } };
}
// Caller creates a fresh controller ID on each dialog lifetime, so a close and
// reopen with the same selection cannot accept a response from the old dialog.
export function initialMemorySwitch(selection: SwitchSelection, controllerId: string, attempt = 0): MemorySwitchState {
  return { scope: { selection: copySelection(selection), controllerId, attempt }, phase: "choosing",
    preparationSequence: -1, interrupt: false };
}
function pending(state: MemorySwitchState): boolean {
  return state.phase === "submitting" || state.phase === "submission_unknown"
    || state.phase === "queued" || state.phase === "switching";
}
export function canConfirmSwitch(state: MemorySwitchState, eligibility: SwitchEligibility): boolean {
  const { selection } = state.scope;
  const proof = state.proof;
  return state.phase === "ready" && !state.submission && !!proof
    && !!proof.preparationId && !!proof.snapshotId
    && Number.isFinite(eligibility.now) && Number.isFinite(proof.expiresAt)
    && proof.expiresAt > eligibility.now && sameSelection(proof.selection, selection)
    && !!selection.sourceGeneration && !!selection.harness
    && selection.harness !== selection.sourceHarness
    && (selection.model.mode === "default" || !!selection.model.id)
    && eligibility.draftClear && eligibility.targetAvailable && eligibility.memoryAvailable
    && !eligibility.pendingOperation;
}
export type MemorySwitchAction =
  | { type: "select"; selection: SwitchSelection }
  | { type: "prepare" }
  | { type: "prepared"; scope: PreparationScope; sequence: number; result: PreparationResult }
  | { type: "interrupt"; checked: boolean; allowed: boolean }
  | { type: "invalidate"; reason: string }
  | { type: "confirm"; eligibility: SwitchEligibility; requestKey: string }
  | { type: "response_lost"; scope: PreparationScope; requestKey: string }
  | { type: "operation"; scope: PreparationScope; result: OperationResult };

export function memorySwitchReducer(state: MemorySwitchState, action: MemorySwitchAction): MemorySwitchState {
  switch (action.type) {
    case "select":
      // Submitted operations retain their exact binding. Card/host navigation
      // creates a new controller; it does not cancel the old backend operation.
      return pending(state) || sameSelection(state.scope.selection, action.selection) ? state
        : initialMemorySwitch(action.selection, state.scope.controllerId, state.scope.attempt + 1);
    case "prepare":
      if (pending(state) || state.scope.selection.harness === state.scope.selection.sourceHarness) return state;
      return { ...initialMemorySwitch(state.scope.selection, state.scope.controllerId, state.scope.attempt + 1), phase: "preparing" };
    case "prepared": {
      if (pending(state) || state.submission || state.phase === "choosing"
        || !sameScope(state.scope, action.scope) || !Number.isSafeInteger(action.sequence)
        || action.sequence <= state.preparationSequence) return state;
      const { result } = action;
      if (result.phase === "ready" && !sameSelection(result.proof.selection, state.scope.selection)) return state;
      // A failed/stale attempt cannot become ready without an explicit refresh.
      if (["failed", "stale_source", "incomplete_source"].includes(state.phase)) return state;
      return { ...state, phase: result.phase, preparationSequence: action.sequence,
        proof: result.phase === "ready" ? { ...result.proof, selection: copySelection(result.proof.selection) } : undefined,
        reason: "reason" in result ? result.reason : undefined,
        interrupt: result.phase === "ready" || result.phase === "preparing" ? state.interrupt : false };
    }
    case "interrupt":
      return pending(state) ? state : { ...state, interrupt: action.allowed && action.checked };
    case "invalidate":
      return pending(state) ? state : { ...state, phase: "stale_source", proof: undefined,
        interrupt: false, reason: action.reason };
    case "confirm": {
      if (!action.requestKey || !canConfirmSwitch(state, action.eligibility)) return state;
      const proof = state.proof!;
      return { ...state, phase: "submitting", submission: {
        scope: { ...state.scope, selection: copySelection(state.scope.selection) },
        requestKey: action.requestKey, proof: { ...proof, selection: copySelection(proof.selection) },
        interrupt: state.interrupt && action.eligibility.canInterrupt,
      } };
    }
    case "response_lost":
      return state.phase === "submitting" && sameScope(state.scope, action.scope)
        && state.submission?.requestKey === action.requestKey
        ? { ...state, phase: "submission_unknown" } : state;
    case "operation": {
      const result = action.result;
      if (!state.submission || !sameScope(state.scope, action.scope)
        || result.requestKey !== state.submission.requestKey || !result.id
        || !Number.isSafeInteger(result.sequence) || result.sequence < 0
        || (state.operation && (state.operation.id !== result.id
          || result.sequence <= state.operation.sequence))) return state;
      // Terminal results do not revert. A queued poll cannot undo start-of-stop.
      if (state.operation && (["completed", "canceled", "failed"].includes(state.operation.phase)
        || (state.operation.phase === "switching" && result.phase === "queued"))) return state;
      return { ...state, phase: result.phase, operation: { ...result }, reason: result.reason,
        proof: undefined, interrupt: false };
    }
  }
}
