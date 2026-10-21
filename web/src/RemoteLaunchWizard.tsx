import { useEffect, useRef, useState } from "react";
import { api } from "./api";
import { desktop, destinationRequest, LaunchDraft } from "./desktop";
import { GitHubRepoPicker } from "./GitHubRepoPicker";
import { sessionRef } from "./hostTransport";
import { OpenCodeModel, OpenCodeSupport, openCodeCommand, useOpenCodeOptions, validOpenCodeModel } from "./OpenCodeOptions";
import { Button, Field, inputStyle, Modal, useToast } from "./ui";

type Project = { id: string; url: string; repository: string; branch: string };
type Preflight = { destination: string; runtime: string; auto_workspace?: boolean };
type Journal = { stage: string; error?: string; session_key?: string };
const newId = () => crypto.randomUUID().replaceAll("-", "");
const agents = [["claude", "Claude Code"], ["codex", "Codex"], ["copilot", "Copilot"], ["opencode", "OpenCode"], ["custom", "Custom…"]];

export function RemoteLaunchWizard({ target, onTarget, onOtherProject, onClose, onCreated, folders, group, initial }: {
  target: string; onTarget: (target: string) => void; onOtherProject: () => void; onClose: () => void;
  onCreated: (key: string, group: string) => void; folders: string[]; group: string; initial: LaunchDraft;
}) {
  const toast = useToast();
  const storage = `remote-coding-wizard:${target}`;
  const [project, setProject] = useState<Project>(() => {
    try {
      const saved = JSON.parse(localStorage.getItem(storage) ?? "null") as Project | null;
      if (saved && /^[a-f0-9]{32}$/.test(saved.id) && typeof saved.url === "string" && typeof saved.branch === "string" && typeof saved.repository === "string") return saved;
    } catch { /* Storage may be unavailable. */ }
    return { id: newId(), url: "", repository: "", branch: "" };
  });
  const [step, setStep] = useState(0);
  const [agent, setAgent] = useState(initial.agent);
  const [custom, setCustom] = useState(initial.command);
  const [name, setName] = useState(initial.name);
  const [prompt, setPrompt] = useState(initial.prompt);
  const [folder, setFolder] = useState(group);
  const [model, setModel] = useState("");
  const openCode = useOpenCodeOptions(agent === "opencode", target);
  const command = agent === "opencode" ? (validOpenCodeModel(model) ? openCodeCommand(model) : "") : agent === "custom" ? custom : agent;
  const blocked = !command.trim() || (agent === "opencode" && !openCode.options?.launch.available);
  const [picker, setPicker] = useState(false);
  const [preflight, setPreflight] = useState<Preflight | null>(null);
  const [busy, setBusy] = useState(false);
  const [progress, setProgress] = useState("");
  const [error, setError] = useState("");
  const [started, setStarted] = useState(false);
  const mounted = useRef(true);
  const running = useRef(false);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  useEffect(() => { try { localStorage.setItem(storage, JSON.stringify(project)); } catch { /* In-memory retry remains available. */ } }, [project, storage]);
  const targetName = desktop()?.targets.find(t => t.id === target)?.name ?? target;
  const repositoryName = project.repository || project.url.replace(/\.git$/, "").split("/").pop() || "project";
  const changeProject = (patch: Partial<Project>) => { setProject(p => ({ ...p, ...patch, id: newId() })); setPreflight(null); setError(""); setStarted(false); };

  async function review() {
    if (running.current) return;
    running.current = true; setBusy(true); setError(""); setProgress("Checking remote workspace and agent…");
    try {
      const checked = await destinationRequest<Preflight>(target, "project-preflight", { id: project.id, url: project.url, branch: project.branch, destination: "", auto_workspace: true, command });
      if (!checked.auto_workspace || !checked.destination) throw new Error("Update DuckTerm on this remote computer to use automatic workspaces.");
      if (mounted.current) { setPreflight(checked); setStep(2); }
    } catch (e) { if (mounted.current) setError((e as Error).message); }
    finally { running.current = false; if (mounted.current) { setBusy(false); setProgress(""); } }
  }
  async function launch() {
    if (!preflight || running.current) return;
    running.current = true; setStarted(true); setBusy(true); setError(""); setProgress("Preparing workspace…");
    let active = true;
    let launchAttempted = false;
    let polling = false;
    const poll = window.setInterval(() => {
      if (polling) return;
      polling = true;
      destinationRequest<Journal>(target, "project-status", { id: project.id }).then(state => {
        if (!active || !mounted.current || state.error) return;
        const labels: Record<string, string> = { cloning: "Cloning repository…", verifying: "Verifying project…", publishing: "Verifying project…", launching: "Starting agent…" };
        if (labels[state.stage]) setProgress(labels[state.stage]);
      }).catch(() => undefined).finally(() => { polling = false; });
    }, 1000);
    try {
      await destinationRequest(target, "project-clone", { ...project, github_repository: project.repository, destination: preflight.destination, auto_workspace: true });
      active = false; clearInterval(poll);
      if (!mounted.current) return;
      setProgress("Starting agent…");
      launchAttempted = true;
      const launched = await destinationRequest<Journal>(target, "project-launch", { id: project.id, command, name: name.trim() || repositoryName.split("/").pop(), prompt, ...(desktop()?.testBuild ? { test: true } : {}) });
      if (!launched.session_key) throw new Error("The remote computer did not return a session. Retry to check its launch status.");
      const key = sessionRef(target, launched.session_key);
      if (folder) await api.setGroup(key, folder);
      if (!mounted.current) return;
      try { localStorage.removeItem(storage); } catch { /* Successful launch does not depend on browser storage. */ }
      window.dispatchEvent(new Event("remote-sessions-refresh"));
      onCreated(key, folder); toast(`Started ${name.trim() || repositoryName}`); onClose();
    } catch (e) { if (mounted.current) { setError((e as Error).message); setStarted(launchAttempted); } }
    finally { active = false; clearInterval(poll); running.current = false; if (mounted.current) { setBusy(false); setProgress(""); } }
  }
  return <Modal title="New remote session" width={520} onClose={() => { if (!busy) onClose(); }}>
    <p style={{ marginTop: 0, color: "var(--muted)" }}>A coding workspace that keeps running remotely.</p>
    <nav aria-label="Setup steps" style={{ display: "flex", gap: 8, marginBottom: 20 }}>
      {["Repository", "Agent & task", "Review"].map((label, i) => <span key={label} aria-current={step === i ? "step" : undefined} style={{ flex: 1, borderBottom: `2px solid ${step === i ? "var(--accent)" : "var(--border)"}`, paddingBottom: 8, fontSize: ".8125rem" }}>{i + 1} · {label}</span>)}
    </nav>
    <div style={{ minHeight: 240 }}>
      {step === 0 && <>
        <Field label="Run on"><select aria-label="Run on" style={inputStyle} value={target} onChange={e => onTarget(e.target.value)}>{desktop()?.targets.map(t => <option key={t.id} value={t.id}>{t.name}</option>)}</select></Field>
        <div style={{ marginBottom: 12 }}><p style={{ fontSize: ".8125rem", marginBottom: 4 }}>GitHub repository</p><Button variant="ghost" onClick={() => setPicker(true)}>{project.repository || "Choose from GitHub…"}</Button></div>
        {picker && <GitHubRepoPicker target={target} onCancel={() => setPicker(false)} onPick={repo => { changeProject({ repository: repo.full_name, url: `https://github.com/${repo.full_name}.git`, branch: repo.default_branch }); setPicker(false); }} />}
        <p style={{ color: "var(--muted)", fontSize: ".875rem" }}>DuckTerm creates a separate workspace automatically. No folder selection needed.</p>
        <details><summary>Other project options</summary>
          <Field label="Repository URL"><input aria-label="Repository URL" style={inputStyle} value={project.url} onChange={e => changeProject({ url: e.target.value, repository: "", branch: "" })} placeholder="https://github.com/owner/project.git" /></Field>
          <Button variant="ghost" onClick={onOtherProject}>Copy a local project or use an existing folder</Button>
        </details>
      </>}
      {step === 1 && <>
        <Field label="Agent"><select aria-label="Agent" style={inputStyle} value={agent} disabled={busy} onChange={e => { setAgent(e.target.value); setPreflight(null); }}>{agents.map(([id, label]) => <option key={id} value={id}>{label}</option>)}</select></Field>
        {agent === "custom" && <Field label="Command"><input aria-label="Command" style={inputStyle} value={custom} disabled={busy} onChange={e => setCustom(e.target.value)} /></Field>}
        {agent === "opencode" && <><OpenCodeModel model={model} onModel={setModel} options={openCode.options} disabled={busy} /><OpenCodeSupport options={openCode.options} error={openCode.error} retry={openCode.retry} /></>}
        <Field label="Task (optional)"><textarea aria-label="Task" style={{ ...inputStyle, height: 85 }} value={prompt} disabled={busy} onChange={e => setPrompt(e.target.value)} placeholder="What should it work on?" /></Field>
        <details><summary>Advanced options</summary>
          <Field label="Session name (optional)"><input aria-label="Session name" style={inputStyle} placeholder={repositoryName} value={name} disabled={busy} onChange={e => setName(e.target.value)} /></Field>
          <Field label="Branch"><input aria-label="Clone branch" style={inputStyle} placeholder="Repository default" value={project.branch} disabled={busy} onChange={e => changeProject({ branch: e.target.value })} /></Field>
          <Field label="Sidebar folder"><select aria-label="Sidebar folder" style={inputStyle} value={folder} disabled={busy} onChange={e => setFolder(e.target.value)}><option value="">Ungrouped</option>{[...new Set([...folders, ...(group ? [group] : [])])].sort().map(f => <option key={f} value={f}>{f}</option>)}</select></Field>
        </details>
      </>}
      {step === 2 && <>
        <dl style={{ display: "grid", gridTemplateColumns: "auto minmax(0, 1fr)", gap: "12px 16px", overflowWrap: "anywhere" }}>
          <dt>Repository</dt><dd style={{ margin: 0 }}>{repositoryName}</dd><dt>Computer</dt><dd style={{ margin: 0 }}>{targetName}</dd><dt>Agent</dt><dd style={{ margin: 0 }}>{agents.find(([id]) => id === agent)?.[1]}</dd><dt>Branch</dt><dd style={{ margin: 0 }}>{project.branch || "Repository default"}</dd>
        </dl>
        <p>Remote agent is available. Repository access and dependencies will be checked while preparing the checkout.</p>
        <details><summary>Workspace location</summary><p style={{ overflowWrap: "anywhere", fontSize: ".8125rem" }}>{preflight?.destination}</p><p>A separate checkout for this session. Existing work stays in place.</p></details>
      </>}
    </div>
    {progress && <div role="status"><p>{progress}</p><progress aria-label="Remote session setup progress" style={{ width: "100%" }} /></div>}
    {error && <p role="alert">{error}</p>}
    <div style={{ display: "flex", justifyContent: "space-between", gap: 8, marginTop: 20 }}>
      <Button variant="ghost" disabled={busy} onClick={() => { if (step && !started) { setStep(step - 1); setError(""); } else onClose(); }}>{step && !started ? "Back" : "Cancel"}</Button>
      <Button disabled={busy || (step === 0 && !project.url.trim()) || (step === 1 && blocked)} onClick={() => { if (step === 0) setStep(1); else if (step === 1) void review(); else void launch(); }}>{busy ? "Working…" : error ? "Retry" : step === 2 ? "Launch session" : "Next →"}</Button>
    </div>
  </Modal>;
}
