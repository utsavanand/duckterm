import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { api, type CheckpointPlan, type CheckpointSelection } from "./api";
import { CHECKPOINT_SELECT, type CheckpointChoiceRequest } from "./checkpointSelection";
import { useCheckpointAttempt } from "./checkpointRequests";
import { splitSessionRef } from "./hostTransport";
import { Modal } from "./ui";
import "./checkpointDialog.css";

export function CheckpointDialogHost() {
  const [request, setRequest] = useState<CheckpointChoiceRequest | null>(null);
  const active = useRef<CheckpointChoiceRequest | null>(null);
  useEffect(() => {
    function open(event: Event) {
      active.current?.resolve(null);
      active.current = (event as CustomEvent<CheckpointChoiceRequest>).detail;
      setRequest(active.current);
    }
    window.addEventListener(CHECKPOINT_SELECT, open);
    return () => { window.removeEventListener(CHECKPOINT_SELECT, open); active.current?.resolve(null); active.current = null; };
  }, []);
  function finish(selection: CheckpointSelection | null) {
    active.current?.resolve(selection); active.current = null; setRequest(null);
  }
  return request && <CheckpointDialog key={request.key} request={request} onFinish={finish} />;
}

function validPlan(value: CheckpointPlan) {
  return value && [value.available_records, value.processed_records, value.pending_records, value.default_records, value.selected_records, value.remaining_records]
    .every(n => Number.isSafeInteger(n) && n >= 0)
    && value.default_records >= 1 && value.default_records <= 1000 && value.batch_records === 20
    && value.available_records === value.processed_records + value.pending_records
    && value.selected_records === Math.min(value.default_records, value.pending_records)
    && value.remaining_records === value.pending_records - value.selected_records
    && value.order === "oldest_unprocessed_first" && value.selection_plan && typeof value.selection_plan === "object" && !Array.isArray(value.selection_plan);
}

export function CheckpointDialog({ request, onFinish }: { request: CheckpointChoiceRequest; onFinish: (selection: CheckpointSelection | null) => void }) {
  const [plan, setPlan] = useState<CheckpointPlan | null>(null);
  const [error, setError] = useState("");
  const [reload, setReload] = useState(0);
  const [choice, setChoice] = useState("default");
  const [custom, setCustom] = useState("");
  const submitted = useRef(false);
  const container = useRef<HTMLDivElement>(null);
  const attempt = useCheckpointAttempt(request.key);
  const running = attempt?.state === "running" || (attempt?.state === "unknown" && attempt.recovering);
  useEffect(() => {
    let live = true;
    setPlan(null); setError(""); setChoice("default");
    void api.checkpointPlan(request.key).then(value => {
      if (!validPlan(value)) throw new Error("Invalid checkpoint count response");
      if (live) { setPlan(value); setCustom(String(value.default_records)); }
    }).catch(cause => { if (live) setError((cause as Error).message); });
    return () => { live = false; };
  }, [request.key, reload]);
  useEffect(() => {
    const previous = document.activeElement;
    container.current?.focus();
    return () => { if (previous instanceof HTMLElement && previous.isConnected) previous.focus(); };
  }, []);
  const count = Number(custom);
  const valid = choice !== "custom" || (custom.trim() !== "" && Number.isInteger(count) && count >= 1 && count <= 1000);
  const selected = plan && valid ? Math.min(plan.pending_records, choice === "all" ? plan.pending_records : choice === "custom" ? count : plan.default_records) : 0;
  const host = splitSessionRef(request.key).host;
  return createPortal(<div ref={container} style={{ position: "fixed", inset: 0, zIndex: 100 }} role="dialog" aria-label="Checkpoint" aria-modal="true" tabIndex={-1} onKeyDown={event => {
    if (event.key === "Escape") { event.preventDefault(); event.stopPropagation(); onFinish(null); }
    if (event.key === "Tab") {
      const items = [...event.currentTarget.querySelectorAll<HTMLElement>("button:not(:disabled),input:not(:disabled)")];
      const first = items[0], last = items[items.length - 1];
      if (event.shiftKey && (document.activeElement === first || document.activeElement === container.current)) { event.preventDefault(); last?.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
    }
  }}><Modal title="Checkpoint" onClose={() => onFinish(null)} width={570}>
    <form className="rd-checkpoint-dialog" onSubmit={event => {
      event.preventDefault();
      if (!plan || !selected || !valid || running || submitted.current) return;
      submitted.current = true;
      onFinish({ plan: plan.selection_plan, records: choice === "all" ? "all" : choice === "custom" ? count : plan.default_records });
    }}>
      <p className="hint">{request.name} · {host === "local" ? "This Mac" : `Remote · ${host}`}</p>
      {!plan && !error && <p role="status">Counting unprocessed conversation records…</p>}
      {error && <div role="alert"><p>Couldn’t count the available history.</p><p className="hint">{error}</p><p className="hint">Nothing has started. Refresh the count before creating a checkpoint.</p><button type="button" className="rd-btn" onClick={() => setReload(n => n + 1)}>Refresh count</button></div>}
      {plan && <>
        <p><strong>{plan.pending_records.toLocaleString()}</strong> conversation records haven’t been summarized.</p>
        <p className="hint">Records are individual user or assistant messages, not whole turns. Tool activity and routine inbox reminders are excluded.</p>
        {plan.pending_records > 0 && <fieldset><legend>How many should this checkpoint summarize?</legend>
          <label><input type="radio" name="checkpoint-choice" checked={choice === "default"} onChange={() => setChoice("default")} /> Next {Math.min(plan.default_records, plan.pending_records).toLocaleString()} · your configured default</label>
          <label><input type="radio" name="checkpoint-choice" checked={choice === "custom"} onChange={() => setChoice("custom")} /> Choose a count <input aria-label="Number of records" type="number" min="1" max="1000" step="1" value={custom} onChange={event => { setChoice("custom"); setCustom(event.target.value); }} /></label>
          <label><input type="radio" name="checkpoint-choice" checked={choice === "all"} onChange={() => setChoice("all")} /> All {plan.pending_records.toLocaleString()} unprocessed records</label>
        </fieldset>}
        <div className="rd-checkpoint-selection" aria-live="polite"><strong>{selected.toLocaleString()}</strong> records selected · <strong>{(plan.pending_records - selected).toLocaleString()}</strong> left for later
          <p className="hint">{selected ? "Batches contain at most 20 records; large messages may need smaller batches." : "Nothing new to summarize."}</p></div>
        {plan.pending_records > 0 && <p className="hint">Oldest unprocessed first. New messages arriving after this dialog opens are left for your next checkpoint.</p>}
        <p className="hint">This choice applies once. Your default in Settings stays at {plan.default_records.toLocaleString()}.</p>
        {!valid && <p role="alert">Enter a whole number from 1 to 1,000.</p>}
      </>}
      {running && <p role="status">A checkpoint is already running for this session.</p>}
      <footer><button className="rd-btn" type="button" onClick={() => onFinish(null)}>Cancel</button><button className="rd-btn rd-btn-primary" type="submit" disabled={!plan || !valid || !selected || !!running}>{selected ? `Checkpoint ${selected.toLocaleString()} records` : "Checkpoint"}</button></footer>
    </form>
  </Modal></div>, document.body);
}
