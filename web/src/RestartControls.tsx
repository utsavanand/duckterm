import { ReactNode, useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { api, ModelChoice, RestartOptions, RestartStatus } from "./api";
import { splitSessionRef } from "./hostTransport";
import { SessionView } from "./types";
import "./restart.css";
import { ModelMenu } from "./ModelMenu";
import { MemorySwitchPanel } from "./MemorySwitchPanel";
import { memorySwitchAvailable } from "./memorySwitchTransport";

const harnessName = (name: string) => ({ "claude-code": "Claude Code", codex: "Codex", gemini: "Gemini CLI" }[name] || name);
type DialogMode = "restart" | "model" | "harness";
type RestartControlProps = { children?: (changeHarness: ReactNode) => ReactNode; session: SessionView; showActions?: boolean; menu?: boolean; onExpanded?: (expanded: boolean) => void; onDismiss?: () => void; onActionComplete?: () => void };
export function RestartControls(props: RestartControlProps) {
  return <SessionRestartControls key={props.session.key} {...props} />;
}
function SessionRestartControls({ children, session, showActions = true, menu = false, onExpanded, onDismiss, onActionComplete }: RestartControlProps) {
  const remote = splitSessionRef(session.key).host !== "local";
  const [status, setStatus] = useState<RestartStatus | null>(null);
  const [statusError, setStatusError] = useState("");
  const [error, setError] = useState("");
  const [open, setOpen] = useState(false);
  const [harness, setHarness] = useState(session.runtime || "generic");
  const [model, setModel] = useState("");
  const [mode, setMode] = useState<DialogMode>("restart");
  const [options, setOptions] = useState<RestartOptions | null>(null);
  const [optionsError, setOptionsError] = useState("");
  const [loadingOptions, setLoadingOptions] = useState(false);
  const [choices, setChoices] = useState<ModelChoice[]>([]);
  const [loadingModels, setLoadingModels] = useState(false);
  const [modelsError, setModelsError] = useState("");
  const [menuAnchor, setMenuAnchor] = useState<HTMLButtonElement | null>(null);
  useLayoutEffect(() => { onExpanded?.(open || !!menuAnchor); }, [open, menuAnchor, onExpanded]);
  const [acting, setActing] = useState(false);
  const actingRef = useRef(false);
  const alive = useRef(true);
  const optionsRequest = useRef(0);
  const statusRequest = useRef(0);
  const opener = useRef<HTMLButtonElement | null>(null);
  const dialog = useRef<HTMLDivElement>(null);
  const currentModel = status?.model || session.model || "";
  const currentHarness = options?.current.harness || session.runtime || "generic";
  const switching = mode === "harness";
  const memoryAvailable = memorySwitchAvailable(options);
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
  useEffect(() => { if (!showActions && !children) { setMenuAnchor(null); setOpen(false); } }, [showActions, children]);
  useEffect(() => { setOpen(false); setMenuAnchor(null); setOptions(null); }, [session.runtime]);
  async function loadModels() {
    setLoadingModels(true); setModelsError("");
    try { const data = await api.models(session.key); if (alive.current) setChoices(data.models); }
    catch (e) { if (alive.current) setModelsError((e as Error).message); }
    finally { if (alive.current) setLoadingModels(false); }
  }
  async function loadOptions(initial?: { model?: string; mode: DialogMode }) {
    const request = ++optionsRequest.current;
    setLoadingOptions(true); setOptionsError("");
    try {
      const data = await api.restartOptions(session.key);
      if (!alive.current || request !== optionsRequest.current) return;
      setOptions(data);
      const requestedMode = initial?.mode ?? mode;
      if (requestedMode === "harness") {
        if (initial || !data.harnesses.some(choice => choice.name === harness && choice.name !== data.current.harness)) {
          const alternatives = data.harnesses.filter(choice => choice.name !== data.current.harness);
          setHarness((alternatives.find(choice => choice.available) || alternatives[0])?.name || ""); setModel("");
        }
      } else if (initial) { setHarness(data.current.harness); setModel(initial.model ?? data.current.model); }
    } catch (e) { if (alive.current && request === optionsRequest.current) setOptionsError((e as Error).message); }
    finally { if (alive.current && request === optionsRequest.current) setLoadingOptions(false); }
  }
  const modelChoices = useMemo(() => currentModel && !choices.some(choice => choice.id === currentModel)
    ? [{ id: currentModel, label: currentModel }, ...choices] : choices, [currentModel, choices]);
  const dialogModels = selected?.models || [];
  const retainedModel = !switching ? options?.current.model || currentModel : "";
  const close = useCallback(() => { if (!actingRef.current) { setOpen(false); opener.current?.focus(); onDismiss?.(); } }, [onDismiss]);
  useEffect(() => { if (open) dialog.current?.focus(); }, [open]);
  function show(button: HTMLButtonElement, nextMode: DialogMode = "restart", picked?: string) {
    opener.current = button;
    setMode(nextMode); setHarness(nextMode === "harness" ? "" : session.runtime || "generic"); setModel(nextMode === "harness" ? "" : picked ?? currentModel);
    setMenuAnchor(null); setError(""); setOptions(null); setOpen(true);
    void loadOptions({ model: picked, mode: nextMode }); void refresh();
  }
  // A missing native conversation must not block discovering other harnesses.
  const draftClear = options?.draft_clear && (status?.can_restart ? status.draft_clear : true);
  const afterTurn = status?.can_restart ? status.after_turn : options?.after_turn;
  const canInterrupt = switching && !!afterTurn && !!options?.supports_interrupt_switch;
  const pathReason = options?.reason || selected?.reason || (switching && options && !harness ? "No other harnesses are available on this Mac." : !switching && options?.resume_restart.reason);
  const allowed = !!selected?.available && (switching ? harness !== currentHarness : !!options?.resume_restart.available)
    && !!draftClear && !pending && !statusError && !optionsError && !loadingOptions && !acting;
  async function restart() {
    if (switching || !allowed || actingRef.current) return;
    actingRef.current = true; setActing(true); ++statusRequest.current;
    try {
      const next = await api.restart(session.key, model, harness);
      if (alive.current) { setStatus(next); setError(""); setOpen(false); opener.current?.focus(); onActionComplete?.(); }
    } catch (e) {
      if (alive.current) { setError((e as Error).message); void loadOptions(); void refresh(); }
    } finally { actingRef.current = false; if (alive.current) setActing(false); }
  }
  async function cancel() {
    if (actingRef.current) return;
    actingRef.current = true; setActing(true); ++statusRequest.current;
    try { const next = status?.memory ? await api.cancelRestart(session.key, status.id) : await api.cancelRestart(session.key); if (alive.current) { setStatus(next); setError(""); void refresh(); } }
    catch (e) { if (alive.current) setError((e as Error).message); }
    finally { actingRef.current = false; if (alive.current) setActing(false); }
  }
  const disabled = remote || pending || acting;
  const reason = remote ? "Restart, Change model and Change harness are available on This Mac only for now." : statusError || status?.reason;
  const action = switching ? harness ? `Switch to ${harnessName(harness)}` : "Switch harness" : "Restart now";
  const changeHarness = <button role={menu ? "menuitem" : undefined} className="rd-btn rd-btn-sm rd-btn-ghost" disabled={disabled} title={remote ? reason : statusError || undefined} onClick={e => show(e.currentTarget, "harness")}>Change harness</button>;
  return <>
    {children?.(changeHarness)}
    {showActions && <button role={menu ? "menuitem" : undefined} className="rd-btn rd-btn-sm rd-btn-primary" disabled={disabled} title={reason} onClick={e => show(e.currentTarget)}>Restart</button>}
    {showActions && changeHarness}
    {showActions && <button role={menu ? "menuitem" : undefined} className="rd-btn rd-btn-sm rd-btn-ghost" disabled={disabled || !status?.can_restart} title={reason || status?.reason} aria-haspopup="menu" aria-expanded={!!menuAnchor} onClick={e => {
      if (menuAnchor) { closeMenu(); return; }
      opener.current = e.currentTarget; setMenuAnchor(e.currentTarget); void loadModels();
    }}>Change model</button>}
    {menuAnchor && <ModelMenu anchor={menuAnchor} choices={modelChoices} current={currentModel} loading={loadingModels} error={modelsError}
      retry={() => void loadModels()} close={closeMenu} select={picked => picked === currentModel ? closeMenu() : show(menuAnchor, "model", picked)} />}
    {reason && showActions && !menu && !pending && <p className="rd-restart-message">{reason}</p>}
    {error && !open && <p className="rd-restart-message" role="alert">{error}</p>}
    {pending && <div className="rd-restart-message rd-restart-notice" role="status">
      {status?.status === "queued" ? status.interrupt ? "Preparing to stop and switch now." : "Restart pending — after this turn." : status?.interrupt ? "Stopping and switching…" : "Restarting…"}
      {status?.requested_harness && <span> Harness: {harnessName(status.requested_harness)}.</span>}
      {status?.requested_model && <span> Model: {status.requested_model}</span>}
      {status?.context === "seeded_new_conversation" && <p>The selected harness continues on this session with its prepared handoff.</p>}
      {status?.status === "queued" && <button role={menu ? "menuitem" : undefined} className="rd-btn rd-btn-sm" disabled={acting} onClick={() => void cancel()}>Cancel restart</button>}
    </div>}
    {status?.status === "failed" && <p className="rd-restart-message" role="alert">{status.error}{status.memory && <><br />{status.process_state === "source_stopped" ? "The previous harness is stopped. Its recovery context is preserved; it has not been restarted automatically." : status.process_state === "source_running" ? "The previous harness is still running." : "The process state could not be verified. Check the terminal before trying again."}</>}</p>}
    {status?.status === "completed" && <p className="rd-restart-message" role="status">{status.context === "seeded_new_conversation"
      ? `Switched to ${harnessName(status.requested_harness || session.runtime || "the selected harness")}. This DuckTerm session continues with its prepared context.`
      : "Restarted — conversation continued."}<br />CLI: {status.previous_cli_version || "Not reported"} → {status.cli_version || "Not reported"}{status.configured_model ? `. Model requested: ${status.configured_model}.` : ""}</p>}
    {open && createPortal(<div className="rd-restart-backdrop" onClick={close}>
      <div ref={dialog} tabIndex={-1} className="rd-restart-dialog" role="dialog" aria-modal="true" aria-labelledby="restart-title" onClick={e => e.stopPropagation()} onKeyDown={e => {
        if (e.key === "Escape") { e.stopPropagation(); close(); }
        if (e.key !== "Tab") return;
        const elements = [...e.currentTarget.querySelectorAll<HTMLElement>("select:not(:disabled),input:not(:disabled),button:not(:disabled),summary,[tabindex='0']")].filter(el => el.getClientRects().length > 0);
        const first = elements[0], last = elements[elements.length - 1];
        if (e.shiftKey && (document.activeElement === first || document.activeElement === dialog.current)) { e.preventDefault(); last?.focus(); }
        else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first?.focus(); }
      }}>
        <header><h2 id="restart-title">{switching ? "Change harness" : "Restart session"}</h2><button className="rd-btn rd-btn-ghost" aria-label={switching ? "Close Change harness" : "Close Restart"} disabled={acting} onClick={close}>×</button></header>
        <p className="rd-restart-subtitle">{session.label} · This Mac{switching && ` · Currently ${harnessName(currentHarness)}`}</p>
        {mode !== "restart" && <div className="rd-restart-pickers">
          {switching && <label>New harness<select value={harness} disabled={acting || loadingOptions || !options} onChange={e => {
            const next = e.target.value; setHarness(next); setModel(""); setError("");
          }}>
            {!harness && <option value="">{loadingOptions ? "Loading harnesses…" : "No other harnesses"}</option>}
            {options?.harnesses.filter(choice => choice.name !== currentHarness).map(choice => <option key={choice.name} value={choice.name}>{harnessName(choice.name)}{choice.available ? "" : " — unavailable"}</option>)}
          </select></label>}
          <label>Model<select value={model} disabled={acting || loadingOptions || !selected?.model_selection.available} onChange={e => setModel(e.target.value)}>
            <option value="">{switching ? "Harness default" : "Keep the current model"}</option>
            {retainedModel && !dialogModels.some(choice => choice.id === retainedModel) && <option value={retainedModel}>{retainedModel}</option>}
            {dialogModels.map(choice => <option key={choice.id} value={choice.id}>{choice.label === choice.id ? choice.id : `${choice.label} · ${choice.id}`}</option>)}
          </select></label>
        </div>}
        {loadingOptions && <p role="status">Checking harnesses and available models…</p>}
        {(optionsError || selected?.model_reason) && <p role="alert">{optionsError || selected?.model_reason} <button className="rd-btn rd-btn-ghost" disabled={loadingOptions || acting} onClick={() => void loadOptions()}>Retry choices</button></p>}
        {mode !== "restart" && selected && !selected.model_selection.available && <p className="rd-restart-help">{selected.model_selection.reason || "This harness does not support model selection."}</p>}
        {switching && memoryAvailable && selected?.available ? <MemorySwitchPanel
          key={JSON.stringify([session.key, options?.current.conversation_generation, harness, model])}
          selection={{ sessionRef: session.key, sourceGeneration: options!.current.conversation_generation!, sourceHarness: currentHarness,
            harness, model: model ? { mode: "explicit", id: model } : { mode: "default" } }}
          targetName={harnessName(harness)} allowed={allowed} afterTurn={!!afterTurn} canInterrupt={!!canInterrupt}
          reason={pathReason || statusError || (!draftClear ? "Send or clear unsent terminal text before switching." : undefined)} close={close}
          onBusy={value => { actingRef.current = value; setActing(value); }}
          onAccepted={(result, submission) => {
            ++statusRequest.current;
            setStatus({ id: result.id, memory: { version: 1 }, status: result.phase === "switching" ? "restarting" : result.phase,
              process_state: result.processState, requested_harness: harness, requested_model: model, configured_model: result.configuredModel || undefined,
              context: "seeded_new_conversation", interrupt: submission.interrupt, error: result.reason });
            setOpen(false); opener.current?.focus(); onActionComplete?.(); void refresh();
          }} /> : <>
        <div className="rd-restart-summary"><strong>{switching ? harness ? `Continue with ${harnessName(harness)}` : "Choose another harness" : "Continue this conversation"}</strong>
          <p>{switching ? "Memory-backed switching needs a supporting backend and an available target harness." : mode === "restart" ? "Restart the current harness and resume this conversation with its current model." : "Restart the current harness and resume this conversation with the selected model."}</p>
          <p>Keep the same session name, project folder and history.</p>
        </div>
        <p className="rd-restart-notice" role={error || pathReason || statusError ? "alert" : "status"}>{error || optionsError || statusError || pathReason || (!draftClear && !loadingOptions ? "Send or clear unsent terminal text before restarting." : afterTurn ? "This will wait until the agent finishes its turn. You can cancel it from the session panel." : "The change will start as soon as you confirm.")}</p>
        {mode === "restart" && <p className="rd-restart-help">To choose another harness, use Change harness.</p>}
        <p className="rd-restart-help">Restart reloads the installed CLI, connectors and session instructions. Background processes started in this terminal may stop.</p>
        <div className="rd-restart-footer"><button className="rd-btn rd-btn-ghost" disabled={acting} onClick={close}>Cancel</button><button className="rd-btn rd-btn-primary" disabled={switching || !allowed} onClick={() => void restart()}>{acting ? "Scheduling…" : afterTurn ? switching ? "Switch after this turn" : "Restart after this turn" : action}</button></div>
        </>}
      </div>
    </div>, document.body)}
  </>;
}
