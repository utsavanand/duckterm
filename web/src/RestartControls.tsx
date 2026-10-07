import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { api, ModelChoice, RestartOptions, RestartStatus } from "./api";
import { splitSessionRef } from "./hostTransport";
import { SessionView } from "./types";
import "./restart.css";
import { ModelMenu } from "./ModelMenu";

const harnessName = (name: string) => ({ "claude-code": "Claude Code", codex: "Codex", gemini: "Gemini CLI" }[name] || name);
export function RestartControls(props: { session: SessionView; showActions?: boolean }) {
  return <SessionRestartControls key={props.session.key} {...props} />;
}
function SessionRestartControls({ session, showActions = true }: { session: SessionView; showActions?: boolean }) {
  const remote = splitSessionRef(session.key).host !== "local";
  const [status, setStatus] = useState<RestartStatus | null>(null);
  const [statusError, setStatusError] = useState("");
  const [error, setError] = useState("");
  const [open, setOpen] = useState(false);
  const [harness, setHarness] = useState(session.runtime || "generic");
  const [model, setModel] = useState("");
  const [options, setOptions] = useState<RestartOptions | null>(null);
  const [optionsError, setOptionsError] = useState("");
  const [loadingOptions, setLoadingOptions] = useState(false);
  const [choices, setChoices] = useState<ModelChoice[]>([]);
  const [loadingModels, setLoadingModels] = useState(false);
  const [modelsError, setModelsError] = useState("");
  const [menuAnchor, setMenuAnchor] = useState<HTMLButtonElement | null>(null);
  const [acting, setActing] = useState(false);
  const actingRef = useRef(false);
  const alive = useRef(true);
  const optionsRequest = useRef(0);
  const statusRequest = useRef(0);
  const opener = useRef<HTMLButtonElement | null>(null);
  const dialog = useRef<HTMLDivElement>(null);
  const currentModel = status?.model || session.model || "";
  const currentHarness = options?.current.harness || session.runtime || "generic";
  const switching = harness !== currentHarness;
  const selected = options?.harnesses.find(choice => choice.name === harness);
  const pending = status?.status === "queued" || status?.status === "restarting";
  const refresh = useCallback(async () => {
    const request = ++statusRequest.current;
    try {
      const next = await api.restartStatus(session.key);
      if (alive.current && request === statusRequest.current) { setStatus(next); setStatusError(""); }
    } catch (e) { if (alive.current && request === statusRequest.current) setStatusError((e as Error).message); }
  }, [session.key]);
  useEffect(() => {
    alive.current = true;
    if (remote) return () => { alive.current = false; };
    void refresh();
    const timer = setInterval(() => { if (!actingRef.current) void refresh(); }, 2000);
    return () => { alive.current = false; clearInterval(timer); };
  }, [refresh, remote]);
  const closeMenu = useCallback(() => { setMenuAnchor(null); opener.current?.focus(); }, []);
  useEffect(() => { if (!showActions) { setMenuAnchor(null); setOpen(false); } }, [showActions]);
  async function loadModels() {
    setLoadingModels(true); setModelsError("");
    try { const data = await api.models(session.key); if (alive.current) setChoices(data.models); }
    catch (e) { if (alive.current) setModelsError((e as Error).message); }
    finally { if (alive.current) setLoadingModels(false); }
  }
  async function loadOptions(initial?: { model?: string }) {
    const request = ++optionsRequest.current;
    setLoadingOptions(true); setOptionsError("");
    try {
      const data = await api.restartOptions(session.key);
      if (!alive.current || request !== optionsRequest.current) return;
      setOptions(data);
      if (initial) { setHarness(data.current.harness); setModel(initial.model ?? data.current.model); }
    } catch (e) { if (alive.current && request === optionsRequest.current) setOptionsError((e as Error).message); }
    finally { if (alive.current && request === optionsRequest.current) setLoadingOptions(false); }
  }
  const modelChoices = useMemo(() => currentModel && !choices.some(choice => choice.id === currentModel)
    ? [{ id: currentModel, label: currentModel }, ...choices] : choices, [currentModel, choices]);
  const dialogModels = selected?.models || [];
  const retainedModel = !switching ? options?.current.model || currentModel : "";
  const close = useCallback(() => { if (!actingRef.current) { setOpen(false); opener.current?.focus(); } }, []);
  useEffect(() => { if (open) dialog.current?.focus(); }, [open]);
  function show(button: HTMLButtonElement, picked?: string) {
    opener.current = button;
    setHarness(session.runtime || "generic"); setModel(picked ?? currentModel);
    setMenuAnchor(null); setError(""); setOptions(null); setOpen(true);
    void loadOptions({ model: picked }); void refresh();
  }
  // A missing native conversation must not block discovering other harnesses.
  const draftClear = options?.draft_clear && (status?.can_restart ? status.draft_clear : true);
  const afterTurn = status?.can_restart ? status.after_turn : options?.after_turn;
  const pathReason = options?.reason || selected?.reason || (!switching && options?.resume_restart.reason);
  const allowed = !!selected?.available && (switching || !!options?.resume_restart.available)
    && !!draftClear && !pending && !statusError && !optionsError && !loadingOptions && !acting;
  async function restart() {
    if (!allowed || actingRef.current) return;
    actingRef.current = true; setActing(true); ++statusRequest.current;
    try {
      const next = await api.restart(session.key, model, harness);
      if (alive.current) { setStatus(next); setError(""); setOpen(false); opener.current?.focus(); }
    } catch (e) {
      if (alive.current) { setError((e as Error).message); void loadOptions(); void refresh(); }
    } finally { actingRef.current = false; if (alive.current) setActing(false); }
  }
  async function cancel() {
    if (actingRef.current) return;
    actingRef.current = true; setActing(true); ++statusRequest.current;
    try { const next = await api.cancelRestart(session.key); if (alive.current) { setStatus(next); setError(""); void refresh(); } }
    catch (e) { if (alive.current) setError((e as Error).message); }
    finally { actingRef.current = false; if (alive.current) setActing(false); }
  }
  const disabled = remote || pending || acting;
  const reason = remote ? "Restart and Change model are available on This Mac only for now." : statusError;
  const action = switching ? `Switch to ${harnessName(harness)}` : "Restart now";
  return <>
    {showActions && <button className="rd-btn rd-btn-sm rd-btn-primary" disabled={disabled} title={reason} onClick={e => show(e.currentTarget)}>Restart</button>}
    {showActions && <button className="rd-btn rd-btn-sm rd-btn-ghost" disabled={disabled || !status?.can_restart} title={reason || status?.reason} aria-haspopup="menu" aria-expanded={!!menuAnchor} onClick={e => {
      if (menuAnchor) { closeMenu(); return; }
      opener.current = e.currentTarget; setMenuAnchor(e.currentTarget); void loadModels();
    }}>Change model <span aria-hidden="true">▾</span></button>}
    {menuAnchor && <ModelMenu anchor={menuAnchor} choices={modelChoices} current={currentModel} loading={loadingModels} error={modelsError}
      retry={() => void loadModels()} close={closeMenu} select={picked => picked === currentModel ? closeMenu() : show(menuAnchor, picked)} />}
    {reason && showActions && !pending && <p className="rd-restart-message">{reason}</p>}
    {error && !open && <p className="rd-restart-message" role="alert">{error}</p>}
    {pending && <div className="rd-restart-message rd-restart-notice" role="status">
      {status?.status === "queued" ? "Restart pending — after this turn." : "Restarting…"}
      {status?.requested_harness && <span> Harness: {harnessName(status.requested_harness)}.</span>}
      {status?.requested_model && <span> Model: {status.requested_model}</span>}
      {status?.context === "seeded_new_conversation" && <p>A new conversation will start with handoff context.</p>}
      {status?.status === "queued" && <button className="rd-btn rd-btn-sm" disabled={acting} onClick={() => void cancel()}>Cancel restart</button>}
    </div>}
    {status?.status === "failed" && <p className="rd-restart-message" role="alert">{status.error}</p>}
    {status?.status === "completed" && <p className="rd-restart-message" role="status">{status.context === "seeded_new_conversation"
      ? `New conversation in ${harnessName(status.requested_harness || session.runtime || "the selected harness")}, seeded from ${harnessName(status.source_harness || "the previous harness")}.`
      : "Restarted — conversation continued."}<br />CLI: {status.previous_cli_version || "Not reported"} → {status.cli_version || "Not reported"}{status.configured_model ? `. Model requested: ${status.configured_model}.` : ""}</p>}
    {open && createPortal(<div className="rd-restart-backdrop" onClick={close}>
      <div ref={dialog} tabIndex={-1} className="rd-restart-dialog" role="dialog" aria-modal="true" aria-labelledby="restart-title" onClick={e => e.stopPropagation()} onKeyDown={e => {
        if (e.key === "Escape") { e.stopPropagation(); close(); }
        if (e.key !== "Tab") return;
        const elements = [...e.currentTarget.querySelectorAll<HTMLElement>("select:not(:disabled),button:not(:disabled)")];
        const first = elements[0], last = elements[elements.length - 1];
        if (e.shiftKey && (document.activeElement === first || document.activeElement === dialog.current)) { e.preventDefault(); last?.focus(); }
        else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first?.focus(); }
      }}>
        <header><h2 id="restart-title">Restart session</h2><button className="rd-btn rd-btn-ghost" aria-label="Close Restart" disabled={acting} onClick={close}>×</button></header>
        <p className="rd-restart-subtitle">{session.label} · This Mac</p>
        <div className="rd-restart-pickers">
          <label>Harness<select value={harness} disabled={acting || loadingOptions || !options} onChange={e => {
            const next = e.target.value; setHarness(next); setModel(next === currentHarness ? options?.current.model || "" : ""); setError("");
          }}>
            {!options?.harnesses.some(choice => choice.name === harness) && <option value={harness}>{harnessName(harness)}</option>}
            {options?.harnesses.map(choice => <option key={choice.name} value={choice.name}>{harnessName(choice.name)}{choice.available ? "" : " — unavailable"}</option>)}
          </select></label>
          <label>Model<select value={model} disabled={acting || loadingOptions || !selected?.model_selection.available} onChange={e => setModel(e.target.value)}>
            <option value="">{switching ? "Harness default" : "Keep the current model"}</option>
            {retainedModel && !dialogModels.some(choice => choice.id === retainedModel) && <option value={retainedModel}>{retainedModel}</option>}
            {dialogModels.map(choice => <option key={choice.id} value={choice.id}>{choice.label === choice.id ? choice.id : `${choice.label} · ${choice.id}`}</option>)}
          </select></label>
        </div>
        {loadingOptions && <p role="status">Checking harnesses and available models…</p>}
        {(optionsError || selected?.model_reason) && <p role="alert">{optionsError || selected?.model_reason} <button className="rd-btn rd-btn-ghost" disabled={loadingOptions || acting} onClick={() => void loadOptions()}>Retry choices</button></p>}
        {selected && !selected.model_selection.available && <p className="rd-restart-help">{selected.model_selection.reason || "This harness does not support model selection."}</p>}
        <div className="rd-restart-summary"><strong>{switching ? `New conversation in ${harnessName(harness)}, seeded from ${harnessName(currentHarness)}` : "Continue this conversation"}</strong>
          <p>{switching ? "This starts a new conversation with handoff context. It does not resume the previous transcript." : "Restart the harness and resume its existing conversation."}</p>
          {switching && <p>Handoff includes the latest checkpoint and saved session notes.</p>}
          <p>Keep the same session name, project folder and history.</p>
        </div>
        <p className="rd-restart-notice" role={error || pathReason || statusError ? "alert" : "status"}>{error || optionsError || statusError || pathReason || (!draftClear && !loadingOptions ? "Send or clear unsent terminal text before restarting." : afterTurn ? "This will wait until the agent finishes its turn. You can cancel it from the Session card." : "The change will start as soon as you confirm.")}</p>
        <p className="rd-restart-help">Restart reloads the installed CLI, connectors and session instructions. Background processes started in this terminal may stop.</p>
        <div className="rd-restart-footer"><button className="rd-btn rd-btn-ghost" disabled={acting} onClick={close}>Cancel</button><button className="rd-btn rd-btn-primary" disabled={!allowed} onClick={() => void restart()}>{acting ? "Scheduling…" : afterTurn ? switching ? "Switch after this turn" : "Restart after this turn" : action}</button></div>
      </div>
    </div>, document.body)}
  </>;
}
