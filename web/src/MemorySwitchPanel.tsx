import { useCallback, useEffect, useRef, useState } from "react";
import { canConfirmSwitch, initialMemorySwitch, memorySwitchReducer, type MemorySwitchAction,
  type OperationResult, type SwitchSelection, type SwitchSubmission } from "./memorySwitchState";
import { memorySwitchService, MemoryTransportError, type MemoryDetails, type MemoryPreparation } from "./memorySwitchTransport";

export function MemorySwitchPanel(props: {
  selection: SwitchSelection; targetName: string; allowed: boolean; reason?: string;
  afterTurn: boolean; canInterrupt: boolean; close: () => void;
  onBusy: (busy: boolean) => void; onAccepted: (result: OperationResult, submission: SwitchSubmission) => void;
}) {
  // The parent keys this component by full session/generation/target/model.
  const [selection] = useState(() => structuredClone(props.selection));
  const [service] = useState(() => memorySwitchService(selection));
  const [state, setState] = useState(() => initialMemorySwitch(selection, crypto.randomUUID()));
  const stateRef = useRef(state);
  const send = useCallback((action: MemorySwitchAction) => { stateRef.current = memorySwitchReducer(stateRef.current, action); setState(stateRef.current); }, []);
  const callbacks = useRef(props); callbacks.current = props;
  const mounted = useRef(true);
  const [attempt, retry] = useState(0);
  const [prepared, setPrepared] = useState<MemoryPreparation | null>(null);
  const [details, setDetails] = useState<MemoryDetails | null>(null);
  const [detailsBusy, setDetailsBusy] = useState(false);
  const detailsPending = useRef(false);
  const [detailsError, setDetailsError] = useState("");
  const [error, setError] = useState("");
  const [now, setNow] = useState(Date.now);
  const lease = useRef({ key: crypto.randomUUID(), id: "", used: false });
  const reconciling = useRef(false);
  const detailsEpoch = useRef(0);
  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; callbacks.current.onBusy(false); };
  }, []);
  useEffect(() => {
    let live = true, reading = false;
    let timer: ReturnType<typeof setTimeout>;
    // Each effect lifetime owns its lease. StrictMode can dispose and replay
    // setup while the first request is still pending; its cleanup must not
    // release the replay's lease. An explicit lost-response retry below keeps
    // its original request key so backend idempotency still applies.
    if (lease.current.used) lease.current = { key: crypto.randomUUID(), id: "", used: false };
    const ownLease = lease.current;
    ownLease.used = true;
    send({ type: "prepare" }); setPrepared(null); setDetails(null); setError(""); setDetailsError("");
    detailsEpoch.current++;
    const scope = stateRef.current.scope;
    function accept(value: MemoryPreparation) {
      if (!live) return;
      send({ type: "prepared", scope, sequence: value.sequence, result: value.result });
      setPrepared(value); setError(""); setNow(Date.now());
      if (value.result.phase !== "ready") { setDetails(null); detailsEpoch.current++; }
    }
    function failed(cause: unknown) {
      if (!live) return;
      setError((cause as Error).message || "Preparation could not be checked.");
      if (cause instanceof MemoryTransportError && ["stale_source", "preparation_expired", "forbidden", "invalid_response"].includes(cause.code)) {
        send({ type: "invalidate", reason: cause.message }); setDetails(null); detailsEpoch.current++;
      }
    }
    async function poll() {
      if (!live || reading || !ownLease.id
        || stateRef.current.submission || !["preparing", "ready"].includes(stateRef.current.phase)) return;
      reading = true;
      try { accept(await service.preparation(ownLease.id)); } catch (cause) { failed(cause); }
      // A mounted operation must learn its terminal outcome even when WebKit
      // reports the window hidden. Slow background reads instead of relying
      // on a visibilitychange event to restart a permanently stopped timer.
      finally { reading = false; if (live) timer = setTimeout(poll, document.visibilityState === "hidden" ? 10000 : 2000); }
    }
    void service.prepare(ownLease.key).then(value => {
      ownLease.id = value.preparationId;
      if (!live) { void service.release(ownLease.id, ownLease.key).catch(() => undefined); return; }
      accept(value); timer = setTimeout(poll, 2000);
    }).catch(failed);
    const visible = () => { clearTimeout(timer); void poll(); };
    document.addEventListener("visibilitychange", visible);
    return () => {
      live = false; clearTimeout(timer); document.removeEventListener("visibilitychange", visible);
      if (ownLease.id) void service.release(ownLease.id, ownLease.key).catch(() => undefined);
    };
  }, [service, attempt, send]);
  function prepareAgain() {
    // If POST was lost, recover that lease instead of duplicating provider work.
    lease.current = { key: lease.current.id ? crypto.randomUUID() : lease.current.key, id: "", used: false };
    retry(value => value + 1);
  }
  const eligibility = { now, draftClear: props.allowed, targetAvailable: props.allowed,
    memoryAvailable: true, pendingOperation: false, canInterrupt: props.canInterrupt };
  const pending = ["submitting", "submission_unknown"].includes(state.phase);
  const ready = canConfirmSwitch(state, eligibility) && !error;
  function accepted(result: OperationResult, submission: SwitchSubmission) {
    if (!mounted.current) return;
    send({ type: "operation", scope: submission.scope, result });
    callbacks.current.onBusy(false); callbacks.current.onAccepted(result, submission);
  }
  async function reconcile(retryUnrecorded = false) {
    const submission = stateRef.current.submission;
    if (!submission || reconciling.current) return;
    reconciling.current = true;
    try { accepted(await service.operation(submission), submission); }
    catch (cause) {
      if (retryUnrecorded && cause instanceof MemoryTransportError && cause.status === 404 && cause.code === "operation_conflict") {
        // The owner explicitly checks again: replay the identical confirmed
        // request, never generate another idempotency key after a lost response.
        try { accepted(await service.switch(submission), submission); }
        catch (retryCause) {
          if (mounted.current) {
            if (retryCause instanceof MemoryTransportError && retryCause.status >= 400 && retryCause.status < 500) {
              send({ type: "rejected", scope: submission.scope, requestKey: submission.requestKey, reason: retryCause.message });
              callbacks.current.onBusy(false); setError("");
            } else setError("Switch status is still unknown. Check again before starting another switch.");
          }
        }
      } else if (mounted.current) setError(`${(cause as Error).message}. Check switch status again before starting another switch.`);
    }
    finally { reconciling.current = false; }
  }
  async function confirm() {
    if (error || stateRef.current.submission) return;
    send({ type: "confirm", requestKey: crypto.randomUUID(), eligibility: { ...eligibility, now: Date.now() } });
    const current = stateRef.current;
    if (current.phase !== "submitting" || !current.submission || pending) return;
    callbacks.current.onBusy(true); setError("");
    const submission = current.submission;
    try { accepted(await service.switch(submission), submission); }
    catch (cause) {
      if (!mounted.current) return;
      // A structured rejection is final for this attempt. Lost/invalid/5xx
      // responses must reconcile the durable receipt without another POST.
      if (cause instanceof MemoryTransportError && cause.status >= 400 && cause.status < 500) {
        send({ type: "rejected", scope: submission.scope, requestKey: submission.requestKey, reason: cause.message });
        setDetails(null); callbacks.current.onBusy(false);
      } else {
        send({ type: "response_lost", scope: submission.scope, requestKey: submission.requestKey });
        setError("The switch response was lost. Checking its saved status…");
        void reconcile();
      }
    }
  }
  async function loadDetails(more = false) {
    const proof = stateRef.current.proof;
    if (!proof || detailsPending.current) return;
    detailsPending.current = true; setDetailsBusy(true); setDetailsError("");
    const epoch = detailsEpoch.current;
    try {
      const result = await service.details(proof.preparationId, proof.snapshotId, more ? details?.nextCursor ?? undefined : undefined);
      if (mounted.current && epoch === detailsEpoch.current && stateRef.current.proof?.snapshotId === proof.snapshotId) {
        setDetails(old => more && old ? { ...result, sources: [...old.sources, ...result.sources], gaps: [...old.gaps, ...result.gaps] } : result);
      }
    } catch (cause) {
      if (mounted.current && epoch === detailsEpoch.current) {
        setDetailsError((cause as Error).message);
        if (cause instanceof MemoryTransportError && ["stale_source", "preparation_expired", "forbidden"].includes(cause.code)) {
          setDetails(null); send({ type: "invalidate", reason: cause.message });
        }
      }
    } finally { detailsPending.current = false; if (mounted.current) setDetailsBusy(false); }
  }
  const title = state.phase === "ready" ? "Ready to switch" : state.phase === "incomplete_source" ? "Some prior history is unavailable"
    : state.phase === "stale_source" ? "Work changed during preparation" : state.phase === "failed" || state.phase === "canceled" ? "Could not prepare the handoff"
      : state.phase === "submission_unknown" ? "Checking switch status" : state.phase === "submitting" ? "Scheduling switch…" : "Preparing the handoff…";
  const handoff = prepared?.coverage.handoff;
  const readyMessage = handoff?.summary_state === "unavailable"
    ? "No saved summary yet. The brief uses current work and recent messages; earlier history stays searchable."
    : handoff ? "The brief uses saved memory and recent messages. Earlier history stays searchable."
      : "Handoff prepared · required context available. Current work and sources are checked again before the switch.";
  const blocked = ["incomplete_source", "stale_source", "failed", "canceled"].includes(state.phase);
  return <>
    <section className="rd-restart-summary"><strong>Continue with {props.targetName}</strong>
      <p>Keep the same session, project folder, tasks, inbox and artifacts. DuckTerm prepares the handoff automatically from your current work and available prior context.</p></section>
    <div className="rd-restart-notice" role={blocked || error ? "alert" : "status"}><strong>{title}</strong>
      <p>{state.reason || (state.phase === "ready" ? readyMessage : pending ? "Waiting for the recorded switch outcome." : "Your current harness keeps running during preparation.")}</p>
      {error && <p>{error}</p>}
      {!pending && (blocked || error) && <button className="rd-btn rd-btn-sm" onClick={prepareAgain}>Prepare again</button>}
      {state.phase === "submission_unknown" && <button className="rd-btn rd-btn-sm" onClick={() => void reconcile(true)}>Check switch status</button>}
    </div>
    {state.phase === "ready" && prepared && <details className="rd-memory-evidence" onToggle={e => { if (e.currentTarget.open && !details) void loadDetails(); }}>
      <summary>Handoff brief and available context</summary>
      <p>The brief is a starting point. Use the read tools for earlier decisions or missing details.</p>
      <dl>{handoff ? <>
        <dt>Saved summary</dt><dd>{handoff.summary_generated_at === null ? "No saved summary" : `Updated ${new Date(handoff.summary_generated_at).toLocaleString()}`}</dd>
        <dt>History summarized</dt><dd>{handoff.summarized_records.toLocaleString()} of {handoff.available_records.toLocaleString()} records</dd>
        <dt>Recent originals in brief</dt><dd>{handoff.included_records.toLocaleString()} {handoff.included_records === 1 ? "record" : "records"}</dd>
        <dt>Additional history</dt><dd>{handoff.omitted_records.toLocaleString()} {handoff.omitted_records === 1 ? "record" : "records"} available through read tools</dd>
        <dt>Originals for retrieval</dt><dd>Retained and searchable</dd>
      </> : <>
        <dt>Available text processed</dt><dd>{prepared.coverage.covered_source_count} of {prepared.coverage.source_count} sources</dd>
        <dt>Originals for retrieval</dt><dd>{prepared.coverage.retrieval}</dd>
        <dt>Retention</dt><dd>{prepared.coverage.retention === "retained_snapshot" ? "Retained source snapshots" : prepared.coverage.retention === "native_conditional" ? "While native originals remain available" : prepared.coverage.retention}</dd>
      </>}
        <dt>Exact target model</dt><dd>{prepared.resolvedModel || "Harness default · not reported"}</dd></dl>
      {handoff && <p>Summarized records were supplied to a summary update. Every detail is not necessarily included in the brief.</p>}
      {(details?.gaps ?? prepared.coverage.gaps).map((g, i) => <p key={`${g.source_id}-${i}`} className="rd-memory-gap">{g.reason}</p>)}
      {prepared.coverage.gap_count > 0 && <p>{prepared.coverage.gap_count} source {prepared.coverage.gap_count === 1 ? "gap" : "gaps"}. Processing available text does not mean every fact is in the brief.</p>}
      {details && <><h3>Automatically prepared brief</h3><div className="rd-memory-brief" role="region" aria-label="Automatically prepared brief" tabIndex={0}>{details.brief.text}</div>
        <p>{details.brief.utf8_bytes.toLocaleString()} of {details.brief.budget_bytes.toLocaleString()} bytes</p>
        <p>{details.sources.length} source references loaded. The target harness receives retrieval instructions.</p>
        {details.nextCursor && <button className="rd-btn rd-btn-sm" disabled={detailsBusy} onClick={() => void loadDetails(true)}>Load more source details</button>}</>}
      {detailsBusy && <p role="status">Loading complete brief…</p>}
      {detailsError && <p role="alert">{detailsError} <button className="rd-btn rd-btn-sm" onClick={() => void loadDetails()}>Retry details</button></p>}
      <p>The target harness uses its own provider conversation. DuckTerm supplies continuity through the brief and accessible prior context.</p>
    </details>}
    {props.canInterrupt && <div className="rd-restart-interrupt"><label><input type="checkbox" disabled={!ready || pending} checked={state.interrupt}
      onChange={e => send({ type: "interrupt", checked: e.target.checked, allowed: props.canInterrupt })} /> Stop the current turn and switch now</label></div>}
    <p className="rd-restart-help">{props.reason || (state.interrupt ? "After you choose Switch, DuckTerm rechecks the handoff before stopping the current turn. Unfinished work and background jobs may stop."
      : props.afterTurn ? "Switch after this turn. Your current harness keeps running until you choose Switch." : "The switch starts after you confirm and the final checks pass.")}</p>
    <p className="rd-restart-help">Reaching a usage limit does not trigger a switch. You choose when to change harnesses.</p>
    <div className="rd-restart-footer"><button className="rd-btn rd-btn-ghost" disabled={pending} onClick={props.close}>Cancel</button>
      <button className="rd-btn rd-btn-primary" disabled={!ready || pending} onClick={() => void confirm()}>{pending ? "Checking switch…" : state.interrupt ? "Stop and switch now" : `Switch to ${props.targetName}`}</button></div>
  </>;
}
