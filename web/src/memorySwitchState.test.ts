import { describe, expect, it } from "vitest";
import { canConfirmSwitch, initialMemorySwitch, memorySwitchReducer as reduce,
  type MemorySwitchState, type OperationResult, type SwitchEligibility, type SwitchSelection } from "./memorySwitchState";

const selection: SwitchSelection = { sessionRef: "session-a", sourceGeneration: "gen-a",
  sourceHarness: "claude-code", harness: "codex", model: { mode: "explicit", id: "gpt-6-astra" } };
const eligible: SwitchEligibility = { now: 100, draftClear: true, targetAvailable: true,
  memoryAvailable: true, pendingOperation: false, canInterrupt: true };
function preparing(): MemorySwitchState { return reduce(initialMemorySwitch(selection, "dialog-1"), { type: "prepare" }); }
function ready(): MemorySwitchState {
  const state = preparing();
  return reduce(state, { type: "prepared", scope: state.scope, sequence: 1, result: {
    phase: "ready", proof: { selection, preparationId: "p1", snapshotId: "s1", expiresAt: 200 },
  } });
}
function submitted(): MemorySwitchState { return reduce(ready(), { type: "confirm", eligibility: eligible, requestKey: "switch1" }); }
function operation(state: MemorySwitchState, phase: OperationResult["phase"], sequence: number,
  processState: OperationResult["processState"] = "source_running"): MemorySwitchState {
  return reduce(state, { type: "operation", scope: state.scope, result: {
    id: "op1", requestKey: "switch1", sequence, phase, processState,
  } });
}

describe("memory switch state guards (not yet wired to the dialog)", () => {
  it("prepares without submitting; only explicit confirmation captures a request", () => {
    expect(preparing().submission).toBeUndefined();
    expect(ready().submission).toBeUndefined();
    const state = submitted();
    expect(state.phase).toBe("submitting");
    expect(state.submission).toMatchObject({ requestKey: "switch1", interrupt: false,
      proof: { snapshotId: "s1" }, scope: { selection } });
    expect(reduce(state, { type: "confirm", eligibility: eligible, requestKey: "switch2" })).toBe(state);
  });
  it.each([
    { harness: "gemini" }, { model: { mode: "default" } },
    { model: { mode: "explicit", id: "gpt-6-sol" } },
    { sourceGeneration: "gen-b" }, { sessionRef: "~remote~72656d6f7465~73657373696f6e2d61" },
  ] satisfies Partial<SwitchSelection>[])("invalidates prepared scope when selection changes: %j", change => {
    const old = ready();
    const next = reduce(old, { type: "select", selection: { ...selection, ...change } });
    expect(next.phase).toBe("choosing");
    expect(next.proof).toBeUndefined();
    expect(reduce(next, { type: "prepared", scope: old.scope, sequence: 9,
      result: { phase: "ready", proof: old.proof! } })).toBe(next);
  });
  it("rejects wrong proof binding and older polling results", () => {
    const state = ready();
    expect(reduce(state, { type: "prepared", scope: state.scope, sequence: 2, result: {
      phase: "ready", proof: { ...state.proof!, selection: { ...selection, sourceGeneration: "wrong" } },
    } })).toBe(state);
    expect(reduce(state, { type: "prepared", scope: state.scope, sequence: 0,
      result: { phase: "preparing" } })).toBe(state);
  });
  it.each(["failed", "incomplete_source", "stale_source"] as const)("%s requires explicit retry, clears proof and interrupt", phase => {
    const old = reduce(ready(), { type: "interrupt", checked: true, allowed: true });
    const blocked = reduce(old, { type: "prepared", scope: old.scope, sequence: 2,
      result: { phase, reason: "Source cannot satisfy the contract" } });
    expect(blocked.proof).toBeUndefined();
    expect(blocked.interrupt).toBe(false);
    expect(canConfirmSwitch(blocked, eligible)).toBe(false);
    expect(reduce(blocked, { type: "prepared", scope: old.scope, sequence: 3,
      result: { phase: "ready", proof: old.proof! } })).toBe(blocked);
    const retry = reduce(blocked, { type: "prepare" });
    expect(retry.phase).toBe("preparing");
    expect(retry.scope.attempt).toBeGreaterThan(old.scope.attempt);
  });
  it.each([
    { draftClear: false }, { targetAvailable: false }, { memoryAvailable: false },
    { pendingOperation: true }, { now: 200 }, { now: Number.NaN },
  ])("blocks final confirmation when eligibility is lost: %j", change => {
    const state = ready();
    expect(reduce(state, { type: "confirm", eligibility: { ...eligible, ...change }, requestKey: "switch1" })).toBe(state);
  });
  it("does not prepare a native same-harness restart or accept empty request keys", () => {
    const same = initialMemorySwitch({ ...selection, harness: selection.sourceHarness }, "dialog-1");
    expect(reduce(same, { type: "prepare" })).toBe(same);
    const state = ready();
    expect(reduce(state, { type: "confirm", eligibility: eligible, requestKey: "" })).toBe(state);
  });
  it("resets interrupt intent on target changes and rechecks capability at confirmation", () => {
    const checked = reduce(ready(), { type: "interrupt", checked: true, allowed: true });
    const changed = reduce(checked, { type: "select", selection: { ...selection, model: { mode: "default" } } });
    expect(changed.interrupt).toBe(false);
    const state = reduce(checked, { type: "confirm", eligibility: { ...eligible, canInterrupt: false }, requestKey: "switch1" });
    expect(state.submission?.interrupt).toBe(false);
  });
  it("invalidates ready proof when new input arrives", () => {
    const state = reduce(ready(), { type: "invalidate", reason: "New owner message" });
    expect(state.phase).toBe("stale_source");
    expect(state.proof).toBeUndefined();
    expect(canConfirmSwitch(state, eligible)).toBe(false);
  });
  it("reconciles a lost submission without permitting a second operation", () => {
    const before = submitted();
    const unknown = reduce(before, { type: "response_lost", scope: before.scope, requestKey: "switch1" });
    expect(unknown.phase).toBe("submission_unknown");
    expect(reduce(unknown, { type: "prepare" })).toBe(unknown);
    expect(reduce(unknown, { type: "confirm", eligibility: eligible, requestKey: "switch2" })).toBe(unknown);
    expect(operation(unknown, "queued", 1).phase).toBe("queued");
    expect(unknown.submission?.requestKey).toBe("switch1");
  });
  it("ignores mismatched operation IDs, request keys, scopes and reordered phases", () => {
    const queued = operation(submitted(), "queued", 1);
    const result = queued.operation!;
    for (const wrong of [{ ...result, requestKey: "other", sequence: 2 }, { ...result, id: "other", sequence: 2 }]) {
      expect(reduce(queued, { type: "operation", scope: queued.scope, result: wrong })).toBe(queued);
    }
    expect(reduce(queued, { type: "operation", scope: { ...queued.scope, attempt: 99 }, result })).toBe(queued);
    const switching = operation(queued, "switching", 2, "source_stopped");
    expect(operation(switching, "queued", 3)).toBe(switching);
    const completed = operation(switching, "completed", 4, "target_running");
    expect(operation(completed, "failed", 5, "unknown")).toBe(completed);
  });
  it("preserves authoritative failure outcomes before and after stopping", () => {
    for (const processState of ["source_running", "source_stopped", "unknown"] as const) {
      const failure = operation(submitted(), "failed", 2, processState);
      expect(failure.operation?.processState).toBe(processState);
      expect(failure.proof).toBeUndefined();
      expect(failure.interrupt).toBe(false);
      expect(canConfirmSwitch(failure, eligible)).toBe(false);
    }
  });
  it("prevents changing target during submission and never mutates the caller's selection", () => {
    const mutable = structuredClone(selection);
    const state = initialMemorySwitch(mutable, "dialog-1");
    mutable.harness = "gemini";
    expect(state.scope.selection.harness).toBe("codex");
    const pending = submitted();
    expect(reduce(pending, { type: "select", selection: { ...selection, harness: "gemini" } })).toBe(pending);
    expect(pending.submission?.scope.selection).not.toBe(pending.scope.selection);
  });
  it("rejects late results after closing and reopening the same session and target", () => {
    const old = ready();
    const next = reduce(initialMemorySwitch(selection, "dialog-2"), { type: "prepare" });
    expect(next.scope.attempt).toBe(old.scope.attempt);
    expect(reduce(next, { type: "prepared", scope: old.scope, sequence: 2,
      result: { phase: "ready", proof: old.proof! } })).toBe(next);
  });
  it("only treats cancellation as final after the server reports it", () => {
    const queued = operation(submitted(), "queued", 1);
    const canceled = operation(queued, "canceled", 2);
    expect(canceled.phase).toBe("canceled");
    expect(operation(canceled, "queued", 3)).toBe(canceled);
    const switching = operation(queued, "switching", 2, "source_stopped");
    expect(switching.phase).toBe("switching");
    expect(switching.operation?.processState).toBe("source_stopped");
  });
});
