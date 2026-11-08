import { useCallback, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { authHeaders } from "./api";
import { AiModelsModal } from "./AiModelsModal";
import { useEdition } from "./edition";
import { canSetUpAgents, desktop, rememberRuntimeSetup, runtimeSetupRequest, type RuntimeSetupEvent,
  type RuntimeSetupStatus, type SetupAgent, type SetupRuntime } from "./desktop";
import { RuntimeSetupTerminal, type RuntimeSetupTerminalHandle } from "./RuntimeSetupTerminal";
import "./runtimeSetup.css";

const AGENTS: { id: SetupAgent; label: string; command: string; runtime?: SetupRuntime }[] = [
  { id: "claude", label: "Claude Code", command: "claude", runtime: "claude-code" },
  { id: "codex", label: "Codex", command: "codex", runtime: "codex" },
  { id: "copilot", label: "Copilot", command: "copilot" },
  { id: "opencode", label: "OpenCode", command: "opencode" },
];
type FoundRuntime = { id: string; installed: boolean; version: string | null; probe: string };
type Stage = "choose" | "install" | "installing" | "signin" | "signing-in" | "checking" | "unknown" | "failed" | "stopped" | "find" | "manual" | "ready";
type Work = { request: string; runtime: SetupRuntime; action: "status" | "install" | "login"; job?: string;
  started: boolean; stopping: boolean; done: Promise<void>; resolve: () => void };
type Selection = { selection_handle: string; display_name: string };

export function RuntimeSetupWizard({ onClose }: { onClose: () => void }) {
  const { id: edition } = useEdition();
  const [selected, setSelected] = useState<SetupAgent>(desktop()?.agentSetup?.default_agent ?? "claude");
  const [custom, setCustom] = useState(desktop()?.agentSetup?.default_agent === "custom" ? desktop()?.agentSetup?.default_command ?? "" : "");
  const [found, setFound] = useState<FoundRuntime[] | null>(null), [discoveryError, setDiscoveryError] = useState(false);
  const [statuses, setStatuses] = useState<Partial<Record<SetupRuntime, RuntimeSetupStatus>>>({});
  const [selections, setSelections] = useState<Partial<Record<SetupRuntime, Selection>>>({});
  const [stage, setStage] = useState<Stage>("choose"), [step, setStep] = useState(0);
  const [busy, setBusy] = useState(false), [stopping, setStopping] = useState(false), [closing, setClosing] = useState(false);
  const [ready, setReady] = useState(false), [error, setError] = useState("");
  const [ai, setAI] = useState<"harness" | "key" | "later">("later"), [aiSettings, setAISettings] = useState(false);
  const [lastAction, setLastAction] = useState<Work["action"]>("status");
  const alive = useRef(true), closingRef = useRef(false), work = useRef<Work | null>(null);
  const terminal = useRef<RuntimeSetupTerminalHandle>(null), dialog = useRef<HTMLDialogElement>(null);
  const geometry = useRef({ cols: 80, rows: 24 });
  const selectionRef = useRef(selections); selectionRef.current = selections;
  const closeRef = useRef(onClose); closeRef.current = onClose;
  const checkRef = useRef<(runtime: SetupRuntime) => Promise<void>>(async () => undefined);
  const agent = AGENTS.find(item => item.id === selected), runtime = agent?.runtime;
  const native = canSetUpAgents(), status = runtime ? statuses[runtime] : undefined;
  const installation = found?.find(item => item.id === (runtime ?? selected));
  const saved = desktop()?.agentSetup;
  const savedExecutable = saved?.default_agent === selected && saved.default_command && saved.default_command !== agent?.command ? saved.default_command : undefined;
  // PATH discovery is not evidence about a previously selected executable.
  const installed = status?.installed ?? (savedExecutable ? undefined : installation?.installed);
  const label = agent?.label ?? "agent", command = agent?.command ?? "zsh";
  const selection = runtime ? selections[runtime] : undefined;
  const installCommand = runtime === "codex" ? "curl -fsSL https://chatgpt.com/codex/install.sh | sh" : "curl -fsSL https://claude.ai/install.sh | bash";
  const loginCommand = runtime === "codex" ? "codex login" : "claude auth login";
  const docs = runtime === "codex" ? "https://developers.openai.com/codex/cli/" : "https://code.claude.com/docs/en/setup";
  const terminalVisible = stage === "installing" || stage === "signing-in" || stage === "failed";

  useEffect(() => {
    const controller = new AbortController();
    void fetch("/runtimes/discovery", { headers: authHeaders(), signal: controller.signal, cache: "no-store" }).then(async response => {
      if (!response.ok) throw new Error();
      const data = await response.json() as { runtimes: FoundRuntime[] };
      if (!Array.isArray(data.runtimes)) throw new Error();
      if (!controller.signal.aborted) setFound(data.runtimes);
    }).catch(() => { if (!controller.signal.aborted) setDiscoveryError(true); });
    return () => controller.abort();
  }, []);

  const finishWork = useCallback((current: Work) => {
    if (work.current !== current) return;
    work.current = null; current.resolve();
    if (alive.current) { setBusy(false); setStopping(false); setReady(false); }
  }, []);

  useEffect(() => {
    alive.current = true;
    const receive = (event: Event) => {
      const frame = (event as CustomEvent<RuntimeSetupEvent>).detail, current = work.current;
      if (!alive.current || !canSetUpAgents() || !current || current.action === "status" || !frame || frame.protocol !== 1 ||
          frame.request_id !== current.request || typeof frame.job_id !== "string" || !frame.job_id ||
          (current.job && current.job !== frame.job_id)) return;
      current.job = frame.job_id;
      if (frame.type === "ready" && frame.runtime === current.runtime && frame.action === current.action) {
        if (!current.stopping) setReady(true);
        void runtimeSetupRequest("resize", { job_id: frame.job_id, ...geometry.current }).catch(() => undefined);
      } else if (frame.type === "output" && typeof frame.data === "string") {
        terminal.current?.write(frame.data);
      } else if (frame.type === "exited" || frame.type === "error") {
        finishWork(current);
        if (closingRef.current) return;
        if (current.stopping || (frame.type === "error" && frame.reason === "cancelled")) setStage("stopped");
        else if (frame.type === "exited" && frame.exit_code === 0) void checkRef.current(current.runtime);
        else { setStage("failed"); setError("Setup didn’t finish. Check again or use the manual instructions."); }
      }
    };
    const targetChanged = () => {
      if (canSetUpAgents()) return;
      // Native owns cancellation on target replacement; stale replies cannot
      // turn this local setup into an action against the newly selected host.
      closingRef.current = true;
      closeRef.current();
    };
    window.addEventListener("runtime-setup", receive);
    window.addEventListener("desktop-targets-changed", targetChanged);
    return () => {
      alive.current = false;
      window.removeEventListener("runtime-setup", receive);
      window.removeEventListener("desktop-targets-changed", targetChanged);
      const current = work.current;
      if (current) { current.stopping = true; void runtimeSetupRequest("cancel", { request_id: current.request }).catch(() => undefined); }
    };
  }, [finishWork]);

  useEffect(() => {
    if (aiSettings) return;
    const panel = dialog.current;
    if (panel && !panel.open) panel.showModal();
    return () => { if (panel?.open) panel.close(); };
  }, [aiSettings]);

  useEffect(() => {
    const heading = dialog.current?.querySelector<HTMLElement>("#rd-setup-heading");
    heading?.setAttribute("tabindex", "-1");
    heading?.focus();
  }, [stage, step, aiSettings]);

  function begin(runtime: SetupRuntime, action: Work["action"]): Work | null {
    if (work.current || closingRef.current || !canSetUpAgents()) return null;
    let resolve = () => undefined as void;
    const done = new Promise<void>(complete => { resolve = complete; });
    const current = { runtime, action, request: crypto.randomUUID(), started: action === "status", stopping: false, done, resolve };
    work.current = current; setBusy(true); setReady(false); setError(""); setLastAction(action);
    return current;
  }

  async function check(runtime: SetupRuntime) {
    const current = begin(runtime, "status"); if (!current) return;
    setStage("checking");
    try {
      const result = await runtimeSetupRequest("status", { runtime, request_id: current.request,
        ...(selectionRef.current[runtime] ? { selection_handle: selectionRef.current[runtime]!.selection_handle } : {}) });
      if (!alive.current || !canSetUpAgents() || work.current !== current || closingRef.current) return;
      if (current.stopping) { setStage("stopped"); return; }
      if (result.type !== "status" || result.protocol !== 1 || result.runtime !== runtime) throw new Error();
      setStatuses(previous => ({ ...previous, [runtime]: result }));
      setStage(!result.installed ? "install" : result.authentication === "signed_in" ? "ready" : result.authentication === "signed_out" ? "signin" : "unknown");
    } catch {
      if (alive.current && work.current === current && !closingRef.current) {
        setStage(current.stopping ? "stopped" : "unknown");
        if (!current.stopping) setError("Couldn’t check the agent. Your current setup has been left in place.");
      }
    } finally { finishWork(current); }
  }
  checkRef.current = check;

  async function start(action: "install" | "login") {
    if (!runtime) return;
    const current = begin(runtime, action); if (!current) return;
    terminal.current?.clear(); setStage(action === "install" ? "installing" : "signing-in");
    // Commit the visible setup terminal before starting the explicit action.
    await new Promise<void>(resolve => requestAnimationFrame(() => resolve()));
    if (!alive.current || !canSetUpAgents() || current.stopping || work.current !== current) { finishWork(current); return; }
    try {
      current.started = true;
      const result = await runtimeSetupRequest("start", { runtime, action, request_id: current.request,
        ...(action === "login" && selection ? { selection_handle: selection.selection_handle } : {}) });
      if (work.current !== current) return; // Early terminal event can precede this reply.
      if (result.request_id !== current.request || (current.job && current.job !== result.job_id)) throw new Error();
      current.job = result.job_id;
      if (current.stopping) await runtimeSetupRequest("cancel", { request_id: current.request });
    } catch {
      if (work.current === current) {
        finishWork(current);
        if (alive.current && !closingRef.current) { setStage("failed"); setError("Couldn’t start setup. Check again or use the manual instructions."); }
      }
    }
  }

  async function stop() {
    const current = work.current; if (!current) return;
    current.stopping = true; setStopping(true); setReady(false);
    if (!current.started) { finishWork(current); setStage("stopped"); return; }
    await runtimeSetupRequest("cancel", { request_id: current.request });
    await current.done; // Do not permit another action before native cleanup finishes.
  }
  async function close(skipped?: boolean) {
    if (closingRef.current) return;
    closingRef.current = true; setClosing(true); setError("");
    try {
      await stop();
      if (skipped !== undefined && native) {
        const state = await runtimeSetupRequest("complete", { skipped, ...(!skipped ? {
          agent: selected, ...(selected === "custom" ? { command: custom.trim() || "zsh" } : {}),
          ...(selection ? { selection_handle: selection.selection_handle } : {}),
        } : {}) });
        rememberRuntimeSetup(state);
      }
      onClose();
    } catch {
      closingRef.current = false; setClosing(false);
      setError("Setup is still stopping or couldn’t save your choice. Try again after it finishes.");
    }
  }

  async function pick() {
    if (!runtime || busy) return;
    setBusy(true); setError("");
    try {
      const result = await runtimeSetupRequest("choose-executable", { runtime });
      if (alive.current && canSetUpAgents() && !closingRef.current && !("cancelled" in result)) setSelections(previous => ({ ...previous, [runtime]: result }));
    } catch { if (alive.current) setError("Couldn’t select an executable. Try again or use manual instructions."); }
    finally { if (alive.current) setBusy(false); }
  }

  function next() {
    if (selected === "custom" || status?.authentication === "signed_in" || (!runtime && installed)) { setStep(1); return; }
    if (!runtime || !native) { setStage("manual"); return; }
    if (installed === false) setStage("install");
    else void check(runtime);
  }
  const sendInput = (data: string) => {
    const current = work.current;
    if (!ready || current?.stopping || !current?.job) return;
    void runtimeSetupRequest("input", { job_id: current.job, data }).catch(() => {
      if (alive.current && work.current === current) setError("Couldn’t send terminal input. Stop setup before trying again.");
    });
  };
  const resize = (cols: number, rows: number) => {
    geometry.current = { cols, rows };
    const current = work.current;
    if (current?.job && !current.stopping) void runtimeSetupRequest("resize", { job_id: current.job, cols, rows }).catch(() => undefined);
  };
  const checkAgain = () => { if (runtime && native) void check(runtime); else setStage("choose"); };
  const minor = <div className="rd-setup-minor"><button onClick={() => setStage("manual")} disabled={busy}>Manual instructions</button>
    {native && runtime && <button onClick={() => setStage("find")} disabled={busy}>Already installed?</button>}</div>;
  const source = runtime && <p className="rd-setup-caption"><a href={docs} target="_blank" rel="noopener noreferrer">{runtime === "codex" ? "OpenAI" : "Anthropic"} installation guide ↗</a></p>;
  const footer = (text: string, action: () => void, back = true, disabled = false) => <div className="rd-setup-actions">
    <button className="rd-setup-link rd-setup-skip" onClick={() => void close(true)} disabled={closing}>Set up later</button>
    {back && <button className="rd-setup-secondary" onClick={() => { setStage("choose"); setError(""); }} disabled={busy || closing}>Back</button>}
    <button className="rd-setup-primary" onClick={action} disabled={disabled || closing}>{closing ? "Stopping…" : text}</button>
  </div>;

  if (aiSettings) return <AiModelsModal onClose={() => setAISettings(false)} />;
  return createPortal(<dialog className="rd-agent-setup" ref={dialog} aria-labelledby="rd-setup-heading"
    onCancel={event => { event.preventDefault(); void close(); }}>
    <header className="rd-setup-titlebar"><span>DuckTerm {edition ? edition[0].toUpperCase() + edition.slice(1) : ""}</span>
      <small>Agent setup</small><button aria-label="Close agent setup" onClick={() => void close()} disabled={closing}>×</button></header>
    <div className="rd-setup-content">
      <ol className="rd-setup-steps"><li aria-current={step === 0 ? "step" : undefined}><b>1</b> Your agent</li><li aria-current={step === 1 ? "step" : undefined}><b>2</b> DuckTerm AI · Optional</li></ol>
      {step === 0 && stage === "choose" && <>
        <h1 id="rd-setup-heading">Choose your agent</h1><p>Use an agent already on your Mac, or install one.</p>
        <div className="rd-setup-agents">{AGENTS.map(item => {
          const checked = item.runtime ? statuses[item.runtime] : undefined;
          const detected = found?.find(row => row.id === (item.runtime ?? item.id));
          const savedPath = saved?.default_agent === item.id && saved.default_command && saved.default_command !== item.command;
          const present = checked?.installed ?? (savedPath ? undefined : detected?.installed);
          return <label className="rd-setup-agent" key={item.id}><input type="radio" name="setup-agent" value={item.id} checked={selected === item.id} onChange={() => { setSelected(item.id); setError(""); }} />
            <span><strong>{item.label}</strong><small>{present ? checked?.version ?? detected?.version ?? "Installed" : present === false ? "Install to get started" : savedPath ? "Saved installation" : "Installation not checked"}</small></span>
            <span className="rd-setup-state">{present === false ? "Not found" : checked?.authentication === "signed_in" ? "Credentials found" : checked?.authentication === "signed_out" ? "Sign-in needed" : "Sign-in not checked"}</span></label>;
        })}<label className="rd-setup-agent"><input type="radio" name="setup-agent" value="custom" checked={selected === "custom"} onChange={() => setSelected("custom")} />
          <span><strong>Another command</strong><small>Use your own CLI or start with a shell</small></span></label></div>
        {selected === "custom" && <label className="rd-setup-field">Command<input value={custom} maxLength={4096} onChange={event => setCustom(event.target.value)} placeholder="zsh" /></label>}
        {discoveryError && <p className="rd-setup-caption">Couldn’t check installed agents. You can still choose one or set up later.</p>}
        {!native && <p className="rd-setup-caption">Guided installation and sign-in are available in the local DuckTerm Mac app.</p>}
        {footer(selected === "custom" || status?.authentication === "signed_in" || (!runtime && installed) ? "Continue" : installed === false && runtime && native ? `Set up ${label}` : !runtime || !native ? "Manual instructions" : "Check setup", next, false)}
      </>}
      {step === 0 && stage === "install" && <><h1 id="rd-setup-heading">Install {label}</h1><p>Run the official installer in a setup terminal. You can watch its progress and stop it.</p>
        <pre className="rd-setup-command">{installCommand}</pre>{source}{minor}{footer(`Install ${label}`, () => void start("install"), true, busy || !native)}</>}
      {step === 0 && stage === "signin" && <><h1 id="rd-setup-heading">Sign in to {label}</h1><p>Continue in your browser with your {runtime === "codex" ? "ChatGPT" : "Claude"} account. Other provider options remain available in {label}.</p>
        <pre className="rd-setup-command">$ {loginCommand}</pre><p className="rd-setup-caption">DuckTerm uses the agent’s own sign-in. API keys for DuckTerm’s internal features are configured separately.</p>
        {footer("Sign in", () => void start("login"), true, busy || !native)}</>}
      {step === 0 && (stage === "installing" || stage === "signing-in") && <><h1 id="rd-setup-heading">{stage === "installing" ? `Installing ${label}` : "Finish signing in"}</h1>
        <p>{stage === "installing" ? "Keep this window open while setup runs." : "Complete the provider’s sign-in in your browser. The agent will report when it finishes."}</p></>}
      {step === 0 && (stage === "failed" || stage === "stopped" || stage === "unknown") && <>
        <h1 id="rd-setup-heading">{stage === "failed" ? "Setup didn’t finish" : stage === "stopped" ? lastAction === "install" ? "Installation stopped" : lastAction === "login" ? "Sign-in stopped" : "Check stopped" : `Couldn’t check ${label}`}</h1>
        <p>{stage === "stopped" ? "Check whether the agent is available before starting again." : "Your current setup has been left in place."}</p>
      </>}
      <RuntimeSetupTerminal ref={terminal} active={step === 0 && terminalVisible} enabled={ready && !stopping && !closing} onInput={sendInput} onResize={resize} />
      {step === 0 && (stage === "installing" || stage === "signing-in") && footer(stopping ? "Stopping…" : stage === "installing" ? "Stop installation" : "Stop sign-in", () => void stop().catch(() => setError("Couldn’t stop setup yet. Try again.")), false, stopping)}
      {step === 0 && stage === "checking" && <><h1 id="rd-setup-heading">Checking {label}</h1><p>Checking the installed version and saved sign-in.</p><div className="rd-setup-status" role="status">{stopping ? "Stopping…" : "Checking…"}</div>
        {footer("Cancel check", () => void stop().catch(() => setError("Couldn’t cancel the check yet. Try again.")), false, stopping)}</>}
      {step === 0 && stage === "find" && <><h1 id="rd-setup-heading">Use your installation</h1><p>If {label} works in your terminal, choose its executable here.</p>
        <label className="rd-setup-field">Executable<input readOnly value={selection?.display_name ?? ""} placeholder={`Choose ${command}…`} /></label>
        <div className="rd-setup-minor"><button onClick={() => void pick()} disabled={busy}>Choose executable…</button></div>
        <p className="rd-setup-caption">The selected executable will be checked before it is used.</p>{footer("Check installation", checkAgain, true, busy || !selection)}</>}
      {step === 0 && stage === "manual" && <><h1 id="rd-setup-heading">Set up {label} yourself</h1><p>{runtime ? "Run these commands in your terminal, then return here." : `Use ${label}’s own installation and sign-in instructions, then select it when creating a session.`}</p>
        {runtime && <pre className="rd-setup-command">{installCommand}{"\n\n"}{loginCommand}</pre>}{source}
        {footer(native && runtime ? "Check again" : "Continue", native && runtime ? checkAgain : () => setStep(1), true, busy)}</>}
      {step === 0 && stage === "ready" && <><h1 id="rd-setup-heading">{label} is ready</h1><div className="rd-setup-status">✓ Installed · Credentials found</div>
        <p>You can select it when creating a session.</p><p className="rd-setup-caption">Account limits and model access are checked by the agent when you use it.</p>{footer("Continue", () => setStep(1))}</>}
      {step === 0 && (stage === "failed" || stage === "stopped" || stage === "unknown") && <>
{minor}
        {stage === "unknown" && runtime && status?.installed && <div className="rd-setup-minor"><button onClick={() => setStage("signin")}>Sign in</button><button onClick={() => setStep(1)}>Continue without checking</button></div>}
        {footer("Check again", checkAgain, true, busy)}</>}
      {step === 1 && <><h1 id="rd-setup-heading">DuckTerm AI · Optional</h1>
        <p>{edition === "lite" ? "Summaries, checkpoints and session memory." : edition === "code" ? "Summaries, checkpoints, memory, PR analysis and folder chat." : "Summaries, checkpoints, memory, PR analysis, folder chat and Oracle."}</p>
        <label className="rd-setup-choice"><input type="radio" name="setup-ai" checked={ai === "harness"} onChange={() => setAI("harness")} /><span><strong>Use an existing agent setup</strong><small>Uses Claude Code or Codex and counts toward its account allowance.</small></span></label>
        <label className="rd-setup-choice"><input type="radio" name="setup-ai" checked={ai === "key"} onChange={() => setAI("key")} /><span><strong>Use my API key or endpoint</strong><small>For DuckTerm’s internal features. Your agents keep their own setup.</small></span></label>
        {ai !== "later" && <div className="rd-setup-detail"><button className="rd-setup-secondary" onClick={() => setAISettings(true)}>{ai === "key" ? "Configure API key…" : "Configure AI models…"}</button>
          <p className="rd-setup-caption">Choose your provider, harness and models in AI settings. Changes apply only when you save there.{ai === "key" ? " Provider usage charges may apply." : ""}</p></div>}
        <label className="rd-setup-choice"><input type="radio" name="setup-ai" checked={ai === "later"} onChange={() => setAI("later")} /><span><strong>Set up later</strong><small>Choose this later in Settings. Your current AI settings stay in place.</small></span></label>
        <div className="rd-setup-actions"><button className="rd-setup-secondary rd-setup-skip" onClick={() => { setStep(0); setStage("choose"); }} disabled={closing}>Back</button>
          <button className="rd-setup-primary" onClick={() => void close(false)} disabled={closing}>{closing ? "Saving…" : "Open DuckTerm"}</button></div></>}
      {error && <p role="alert" className="rd-setup-error">{error}</p>}
    </div>
  </dialog>, document.body);
}
