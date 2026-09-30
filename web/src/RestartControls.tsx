import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { api, ModelChoice, RestartStatus } from "./api";
import { splitSessionRef } from "./hostTransport";
import { SessionView } from "./types";
import { Modal } from "./ui";
import "./restart.css";
import { ModelMenu } from "./ModelMenu";

export function RestartControls({ session, showActions = true }: { session: SessionView; showActions?: boolean }) {
  const remote = splitSessionRef(session.key).host !== "local";
  const [status, setStatus] = useState<RestartStatus | null>(null);
  const [error, setError] = useState("");
  const [open, setOpen] = useState(false);
  const [model, setModel] = useState("");
  const [choices, setChoices] = useState<ModelChoice[]>([]);
  const [loadingModels, setLoadingModels] = useState(false);
  const [modelsError, setModelsError] = useState("");
  const [menuAnchor, setMenuAnchor] = useState<HTMLButtonElement | null>(null);
  const [changingModel, setChangingModel] = useState(false);
  const [acting, setActing] = useState(false);
  const opener = useRef<HTMLButtonElement | null>(null);
  const modelInput = useRef<HTMLSelectElement>(null);
  const current = useRef(session.key);
  current.current = session.key;
  const refresh = useCallback(async () => {
    const key = session.key;
    try {
      const next = await api.restartStatus(key);
      if (current.current === key) { setStatus(next); setError(""); }
    } catch (e) { if (current.current === key) setError((e as Error).message); }
  }, [session.key]);
  useEffect(() => {
    setStatus(null); setOpen(false); setError("");
    setChoices([]); setMenuAnchor(null); setModelsError(""); setLoadingModels(false);
    if (remote) return;
    void refresh();
    const timer = setInterval(() => { void refresh(); }, 2000);
    return () => clearInterval(timer);
  }, [refresh, remote]);
  const closeMenu = useCallback(() => { setMenuAnchor(null); opener.current?.focus(); }, []);
  useEffect(() => { if (!showActions) setMenuAnchor(null); }, [showActions]);
  async function loadModels() {
    const key = session.key;
    setLoadingModels(true); setModelsError("");
    try {
      const data = await api.models(key);
      if (current.current === key) setChoices(data.models);
    } catch (e) { if (current.current === key) setModelsError((e as Error).message); }
    finally { if (current.current === key) setLoadingModels(false); }
  }
  const currentModel = status?.model || session.model || "";
  const modelChoices = useMemo(() => currentModel && !choices.some(choice => choice.id === currentModel)
    ? [{ id: currentModel, label: currentModel }, ...choices] : choices, [currentModel, choices]);
  const close = useCallback(() => { setOpen(false); opener.current?.focus(); }, []);
  useEffect(() => {
    if (!open) return;
    modelInput.current?.focus();
    const escape = (e: KeyboardEvent) => { if (e.key === "Escape") { e.stopPropagation(); close(); } };
    window.addEventListener("keydown", escape);
    return () => window.removeEventListener("keydown", escape);
  }, [open, close]);
  async function show(button: HTMLButtonElement, picked?: string) {
    opener.current = button;
    setModel(picked ?? currentModel);
    setChangingModel(picked !== undefined);
    setMenuAnchor(null);
    if (!choices.length && !loadingModels) void loadModels();
    setOpen(true);
    await refresh();
  }
  async function restart() {
    setActing(true);
    try { setStatus(await api.restart(session.key, model)); setError(""); close(); }
    catch (e) { setError((e as Error).message); }
    finally { setActing(false); }
  }
  async function cancel() {
    setActing(true);
    try { setStatus(await api.cancelRestart(session.key)); setError(""); await refresh(); }
    catch (e) { setError((e as Error).message); }
    finally { setActing(false); }
  }
  const pending = status?.status === "queued" || status?.status === "restarting";
  const disabled = remote || !status?.can_restart || pending || acting || !!error;
  const reason = remote ? "Restart and Change model are available on This Mac only for now." : error || status?.reason;
  return <>
    {showActions && <button className="rd-btn rd-btn-sm rd-btn-primary" disabled={disabled} title={reason} onClick={e => void show(e.currentTarget)}>Restart</button>}
    {showActions && <button className="rd-btn rd-btn-sm rd-btn-ghost" disabled={disabled} title={reason} aria-haspopup="menu" aria-expanded={!!menuAnchor} onClick={e => {
      if (menuAnchor) { closeMenu(); return; }
      opener.current = e.currentTarget; setMenuAnchor(e.currentTarget); void loadModels();
    }}>Change model <span aria-hidden="true">▾</span></button>}
    {menuAnchor && <ModelMenu anchor={menuAnchor} choices={modelChoices} current={currentModel} loading={loadingModels} error={modelsError}
      retry={() => void loadModels()} close={closeMenu} select={picked => {
        if (picked === currentModel) closeMenu();
        else void show(menuAnchor, picked);
      }} />}
    {reason && showActions && !pending && <p className="rd-restart-message">{reason}</p>}
    {pending && <div className="rd-restart-message rd-restart-notice" role="status">
      {status?.status === "queued" ? "Restart pending — after this turn." : "Restarting…"}
      {status?.requested_model && <span> Model: {status.requested_model}</span>}
      {status?.status === "queued" && <button className="rd-btn rd-btn-sm" disabled={acting} onClick={() => void cancel()}>Cancel restart</button>}
    </div>}
    {status?.status === "failed" && <p className="rd-restart-message" role="alert">{status.error}</p>}
    {status?.status === "completed" && <p className="rd-restart-message" role="status">Restarted — conversation continued.<br />CLI: {status.previous_cli_version || "Not reported"} → {status.cli_version || "Not reported"}{status.configured_model ? `. Model requested: ${status.configured_model}.` : ""}</p>}
    {open && createPortal(<Modal title={changingModel ? "Change model" : "Restart session"} onClose={close}>
      <div className="rd-restart-dialog" role="dialog" aria-modal="true" aria-label={changingModel ? "Change model" : "Restart session"} onKeyDown={e => {
        if (e.key !== "Tab") return;
        const elements = [...e.currentTarget.querySelectorAll<HTMLElement>("select:not(:disabled),button:not(:disabled)")];
        const first = elements[0], last = elements[elements.length - 1];
        if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last?.focus(); }
        else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first?.focus(); }
      }}>
        <p className="rd-restart-subtitle">{session.label} · {session.runtime} · This Mac</p>
        <div className="rd-restart-summary"><strong>Continue this conversation</strong><p>Keep the same session, project folder and history.</p></div>
        <label htmlFor="restart-model">Model after restart</label>
        <select ref={modelInput} id="restart-model" value={model} onChange={e => setModel(e.target.value)} disabled={acting || loadingModels}>
          {!currentModel && <option value="">Keep the current model</option>}
          {modelChoices.map(choice => <option key={choice.id} value={choice.id}>{choice.label} · {choice.id}</option>)}
        </select>
        {loadingModels && <p role="status">Loading available models…</p>}
        {modelsError && <p role="alert">{modelsError} <button className="rd-btn rd-btn-ghost" onClick={() => void loadModels()}>Retry model list</button></p>}
        <p className="rd-restart-help">Choose a model for this conversation. Changing it requires a restart; future restarts keep your choice.</p>
        <p className="rd-restart-notice" role={error ? "alert" : "status"}>{error || status?.reason || (status?.after_turn ? "Restart will wait until the agent finishes a turn. You can cancel it from the Session card." : "You’ll see the CLI version used after restart.")}</p>
        <p>Restart reloads the installed CLI, connectors and session instructions. Background processes started in this terminal may stop.</p>
        <div className="rd-restart-footer"><button className="rd-btn rd-btn-ghost" onClick={close}>Cancel</button><button className="rd-btn rd-btn-primary" disabled={acting || !status?.can_restart || !status?.draft_clear || !!error} onClick={() => void restart()}>{acting ? "Scheduling…" : status?.after_turn ? "Restart after this turn" : "Restart now"}</button></div>
      </div>
    </Modal>, document.body)}
  </>;
}
