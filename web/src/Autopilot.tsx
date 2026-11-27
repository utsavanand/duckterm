import { ReactNode, useCallback, useEffect, useState } from "react";
import {
  AutopilotDraft, AutopilotEntry, AutopilotFlow, AutopilotOverview, AutopilotRun, AutopilotSummary, autopilotApi, TriggerType,
} from "./autopilotApi";
import { api } from "./api";
import { Button, inputStyle, Modal } from "./ui";
import "./autopilot.css";

const POLL_MS = 5000;
const SUMMARY_POLL_MS = 15000;

// The sidebar row's counts. Off (null) when the flag is off.
export function useAutopilotSummary(enabled: boolean): AutopilotSummary | null {
  const [summary, setSummary] = useState<AutopilotSummary | null>(null);
  useEffect(() => {
    if (!enabled) { setSummary(null); return; }
    let stopped = false;
    let timer: ReturnType<typeof setTimeout>;
    const tick = async () => {
      try {
        const next = await autopilotApi.summary();
        if (!stopped) setSummary(next);
      } catch {
        if (!stopped) setSummary(null);
      }
      if (!stopped) timer = setTimeout(tick, SUMMARY_POLL_MS);
    };
    void tick();
    return () => { stopped = true; clearTimeout(timer); };
  }, [enabled]);
  return summary;
}

const TRIGGERS: Record<TriggerType, { name: string; glyph: string; hint: string }> = {
  "github-issue": { name: "GitHub issues", glyph: "GH", hint: "From people with write access to the issue repository." },
  "github-pr": { name: "GitHub pull requests", glyph: "GH", hint: "Opened or updated. Drafts are skipped." },
  "github-ci": { name: "Failed CI runs", glyph: "GH", hint: "GitHub Actions on the branch you pick." },
  "jira-issue": { name: "Jira issues", glyph: "J", hint: "When an issue reaches the status you pick." },
  "slack-message": { name: "Slack messages", glyph: "#", hint: "When someone you allow reacts with the emoji." },
  schedule: { name: "Schedule", glyph: "⏱", hint: "" },
};

const STATE_LABEL: Record<AutopilotRun["state"], string> = {
  starting: "Starting", running: "Running", waiting_gate: "Needs you", succeeded: "Done",
  failed: "Failed", cancelled: "Cancelled", interrupted: "Interrupted",
};

const flowTitle = (name: string) => name.charAt(0).toUpperCase() + name.slice(1).replace(/-/g, " ");

function ago(ms: number): string {
  const s = Math.max(0, Math.round((Date.now() - ms) / 1000));
  if (s < 60) return `${s} s ago`;
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  return `${Math.round(s / 3600)} h ago`;
}

function triggerLine(kind: TriggerType, settings: { labels: string[] } | undefined): string {
  if (kind === "github-issue") {
    const labels = settings?.labels ?? [];
    return labels.length ? `GitHub issues labelled ${labels.join(", ")}` : "Every new GitHub issue";
  }
  return TRIGGERS[kind].name;
}

function TriggerChips({ entry }: { entry: AutopilotEntry }) {
  return <div className="ap-chips">
    {(Object.keys(entry.triggers) as TriggerType[]).map(kind => <span key={kind} className="ap-chip">
      {triggerLine(kind, entry.triggers[kind])}
    </span>)}
  </div>;
}

export function AutopilotPage() {
  const [data, setData] = useState<AutopilotOverview | null>(null);
  const [error, setError] = useState("");
  const [setup, setSetup] = useState<{ flow: AutopilotFlow; editing: AutopilotEntry | null } | null>(null);
  const [runOnce, setRunOnce] = useState<AutopilotEntry | null>(null);
  const [openRun, setOpenRun] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setData(await autopilotApi.overview());
      setError("");
    } catch (e) {
      setError((e as Error).message);
    }
  }, []);

  useEffect(() => {
    let stopped = false;
    let timer: ReturnType<typeof setTimeout>;
    const tick = async () => {
      await load();
      if (!stopped) timer = setTimeout(tick, POLL_MS);
    };
    void tick();
    return () => { stopped = true; clearTimeout(timer); };
  }, [load]);

  async function toggle(entry: AutopilotEntry) {
    try {
      await autopilotApi.update(entry.id, { on: !entry.on });
      await load();
    } catch (e) {
      setError((e as Error).message);
    }
  }

  if (!data) return <div className="ap-page"><div className="ap-col"><h1>Autopilot</h1><p className="ap-muted">{error || "Loading…"}</p></div></div>;
  const on = data.autopilots.filter(a => a.on).length;
  const paused = data.autopilots.length - on;
  const flowsById = new Map(data.flows.map(f => [f.id, f]));
  const inUse = data.autopilots.length > 0 || data.runs.length > 0;
  const { waiting_gate, active, done_today, failed_today } = data.summary;

  return <div className="ap-page"><div className="ap-col">
    <h1>Autopilot</h1>
    {error && <p className="ap-error" role="alert">{error}</p>}
    {data.problems.length > 0 && <p className="ap-warn">Runs can't start yet: {data.problems.join("; ")}.</p>}
    {inUse && <div className="ap-status">
      <span className={`ap-pill${waiting_gate ? " ap-pill-wait" : ""}`}><b>{waiting_gate}</b> need you</span>
      <span className="ap-pill"><b>{active}</b> running</span>
      <span className="ap-pill"><b>{done_today}</b> done today</span>
      <span className={`ap-pill${failed_today ? " ap-pill-bad" : ""}`}><b>{failed_today}</b> failed today</span>
    </div>}

    {data.autopilots.length > 0 && <section className="ap-sec">
      <h2>On autopilot <small>{on} on{paused ? ` · ${paused} paused` : ""}</small></h2>
      <div className="ap-list">{data.autopilots.map(entry => <AutopilotRow key={entry.id} entry={entry} flowsById={flowsById}>
        <button type="button" className="ap-link" onClick={() => setRunOnce(entry)}>Run once…</button>
        <button type="button" className="ap-link" onClick={() => void toggle(entry)}>{entry.on ? "Pause" : "Resume"}</button>
        <Button size="sm" variant="ghost" disabled={!flowsById.has(entry.flow)}
          onClick={() => setSetup({ flow: flowsById.get(entry.flow)!, editing: entry })}>Edit</Button>
      </AutopilotRow>)}</div>
    </section>}

    <section className="ap-sec">
      <h2>Flows</h2>
      {data.flows.length === 0
        ? <p className="ap-muted">No flows declare triggers yet.</p>
        : <div className="ap-list">{data.flows.map(flow => <div key={flow.id} className="ap-item">
          <div className="ap-item-main">
            <h3>{flowTitle(flow.name)} <span className="ap-src">{flowTitle(flow.kit)} kit</span></h3>
            {flow.description && <div className="ap-meta">{flow.description}</div>}
            <div className="ap-chips">{flow.triggers.map(k => <span key={k} className={`ap-chip${data.triggers[k]?.available ? "" : " ap-chip-off"}`}>
              {TRIGGERS[k].name}{data.triggers[k]?.available ? "" : " · soon"}
            </span>)}</div>
          </div>
          <div className="ap-acts"><Button size="sm" variant="ghost"
            onClick={() => setSetup({ flow, editing: null })}>Set up</Button></div>
        </div>)}</div>}
    </section>

    {inUse && <section className="ap-sec">
      <h2>Runs</h2>
      {data.runs.length === 0
        ? <p className="ap-muted">No runs yet.</p>
        : <RunTable runs={data.runs} openRun={openRun} setOpenRun={setOpenRun} />}
    </section>}

    {setup && <SetupDialog overview={data} flow={setup.flow} editing={setup.editing} onClose={() => setSetup(null)}
      onSaved={() => { setSetup(null); void load(); }} />}
    {runOnce && <RunOnceDialog entry={runOnce} onClose={() => setRunOnce(null)}
      onStarted={() => { setRunOnce(null); void load(); }} />}
  </div></div>;
}

function AutopilotRow({ entry, flowsById, workspace, children }: {
  entry: AutopilotEntry; flowsById: Map<string, AutopilotFlow>; workspace?: string; children?: ReactNode;
}) {
  const where = entry.workspace && entry.workspace !== workspace ? <> · in {entry.workspace}</> : null;
  const status = entry.watch.error ? <span className="ap-error-text">{entry.watch.error}</span>
    : entry.active ? `${entry.active} running` : entry.watch.checked_at ? `checked ${ago(entry.watch.checked_at)}` : "not checked yet";
  return <div className="ap-item">
    <div className="ap-item-main">
      <h3>{entry.name} <span className={entry.on ? "ap-on" : "ap-paused"}>{entry.on ? "On" : "Paused"}</span></h3>
      <div className="ap-meta">{flowTitle(flowsById.get(entry.flow)?.name ?? entry.flow)} · <b>{entry.repo}</b>{issueRepo(entry) !== entry.repo && <> · issues from <b>{issueRepo(entry)}</b></>}{where} · {status}</div>
      <TriggerChips entry={entry} />
    </div>
    {children && <div className="ap-acts">{children}</div>}
  </div>;
}

function RunTable({ runs, openRun, setOpenRun }: {
  runs: AutopilotRun[]; openRun: string | null; setOpenRun: (f: (id: string | null) => string | null) => void;
}) {
  return <div className="ap-table-wrap"><table className="ap-table">
    <thead><tr><th>Issue</th><th>Autopilot</th><th>Step</th><th>State</th><th className="ap-num-col">Tokens</th><th>Started</th></tr></thead>
    <tbody>{runs.map(run => <RunRow key={run.id} run={run} open={openRun === run.id}
      onToggle={() => setOpenRun(id => id === run.id ? null : run.id)} />)}</tbody>
  </table></div>;
}

function RunRow({ run, open, onToggle }: { run: AutopilotRun; open: boolean; onToggle: () => void }) {
  const live = run.state === "running" || run.state === "starting";
  const step = live ? run.current ?? "starting" : "—";
  return <>
    <tr className="ap-row" onClick={onToggle} aria-expanded={open} tabIndex={0}
      onKeyDown={e => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onToggle(); } }}>
      <td>{run.item.number ? <span className="ap-num">#{run.item.number}</span> : null} {run.item.title ?? run.id}</td>
      <td className="ap-muted">{run.autopilot_name}{run.workspace ? <span className="ap-small"> · {run.workspace}</span> : null}</td>
      <td>{step}</td>
      <td><span className={`ap-state ap-state-${run.state}`}>{run.pr ? "PR open" : STATE_LABEL[run.state] ?? run.state}</span></td>
      <td className="ap-mono ap-num-col">{run.tokens != null ? `${Math.round(run.tokens / 1000)}k` : "—"}</td>
      <td className="ap-muted">{new Date(run.started_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}</td>
    </tr>
    {open && <tr className="ap-detail"><td colSpan={6}>
      <div className="ap-steps">
        {(run.steps ?? []).map(s => <span key={`${s.step}-${s.attempt}`} className={`ap-step${s.exit ? " ap-step-bad" : ""}`}>
          {s.step}{s.attempt > 1 ? ` (attempt ${s.attempt})` : ""}{s.choice ? `: ${s.choice}` : ""}
        </span>)}
        {(run.steps ?? []).length === 0 && <span className="ap-muted">No steps finished yet.</span>}
      </div>
      {run.error && <p className="ap-error-text">{run.error}</p>}
      {run.pr_error && <p className="ap-error-text">Could not open the pull request: {run.pr_error}</p>}
      {run.pr && <p className="ap-small"><a href={run.pr.url} target="_blank" rel="noreferrer">Draft pull request #{run.pr.number}</a> into {run.pr.base}</p>}
      {run.pr_skipped && <p className="ap-muted ap-small">No pull request: {run.pr_skipped}.</p>}
      <p className="ap-muted ap-small">
        Branch <code>{run.branch}</code>{run.pushed ? " on GitHub" : " on this Mac"}{run.item.started_by ? ` · started by ${run.item.started_by}` : ""}
        {run.item.url && <> · <a href={run.item.url} target="_blank" rel="noreferrer">Open the issue</a></>}
      </p>
    </td></tr>}
  </>;
}

function RunOnceDialog({ entry, onClose, onStarted }: { entry: AutopilotEntry; onClose: () => void; onStarted: () => void }) {
  const [issue, setIssue] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  async function start() {
    setBusy(true);
    try {
      await autopilotApi.runOnce(entry.id, Number(issue.replace(/^#/, "")));
      onStarted();
    } catch (e) {
      setError((e as Error).message);
      setBusy(false);
    }
  }
  return <Modal title={`Run ${entry.name} once`} onClose={onClose}>
    <label className="ap-field"><span>Issue number in {issueRepo(entry)}</span>
      <input style={inputStyle} value={issue} onChange={e => setIssue(e.target.value)} placeholder="#42" autoFocus
        onKeyDown={e => { if (e.key === "Enter" && issue.trim()) void start(); }} /></label>
    {error && <p className="ap-error-text">{error}</p>}
    <div className="ap-foot">
      <Button variant="ghost" onClick={onClose}>Cancel</Button>
      <Button onClick={() => void start()} disabled={busy || !issue.trim()}>Start</Button>
    </div>
  </Modal>;
}

const issueRepo = (entry: AutopilotEntry) => entry.issue_repo || entry.repo;

// Another autopilot that would pick up the same issue: same issue repository
// and a shared label (no labels means every issue).
function labelClash(others: AutopilotEntry[], self: string | undefined, repo: string, labels: string[]) {
  for (const other of others) {
    const theirs = other.triggers["github-issue"]?.labels;
    if (other.id === self || !theirs || issueRepo(other) !== repo) continue;
    const shared = !labels.length || !theirs.length ? labels[0] ?? theirs[0] ?? "every issue" : labels.find(l => theirs.includes(l));
    if (shared) return { name: other.name, repo, label: shared };
  }
  return null;
}

const emptyDraft = (flow: AutopilotFlow, overview: AutopilotOverview): AutopilotDraft => {
  const first = flow.triggers.find(k => overview.triggers[k]?.available);
  return {
    flow: flow.id, name: "", workspace: "", repo: "", issue_repo: "",
    triggers: first ? { [first]: { labels: first === "github-issue" ? ["autopilot"] : [] } } : {},
    approvals: { spec_on_visible: false, keep_merge_approval: false }, budget_m: 5, max_runs: 2,
  };
};

function SetupDialog({ overview, flow, editing, onClose, onSaved }: {
  overview: AutopilotOverview; flow: AutopilotFlow; editing: AutopilotEntry | null; onClose: () => void; onSaved: () => void;
}) {
  const [step, setStep] = useState<"triggers" | "confirm">("triggers");
  const [draft, setDraft] = useState<AutopilotDraft>(editing ? {
    flow: editing.flow, name: editing.name, workspace: editing.workspace ?? "", repo: editing.repo,
    issue_repo: issueRepo(editing) === editing.repo ? "" : issueRepo(editing), triggers: editing.triggers,
    approvals: editing.approvals, budget_m: editing.budget_m, max_runs: editing.max_runs,
  } : emptyDraft(flow, overview));
  const [repos, setRepos] = useState<string[] | null>(null);
  const [workspaces, setWorkspaces] = useState<string[]>([]);
  const [labelsText, setLabelsText] = useState((editing?.triggers["github-issue"]?.labels ?? ["autopilot"]).join(", "));
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let stopped = false;
    autopilotApi.repositories().then(list => {
      if (stopped) return;
      setRepos(list);
      setDraft(d => d.repo || !list.length ? d : { ...d, repo: list[0] });
    }).catch(() => { if (!stopped) setRepos([]); });
    api.folders().then(r => { if (!stopped) setWorkspaces(r.folders); }).catch(() => undefined);
    return () => { stopped = true; };
  }, []);

  const anyOn = Object.keys(draft.triggers).length > 0;
  const labels = () => labelsText.split(",").map(s => s.trim()).filter(Boolean);
  const clash = draft.triggers["github-issue"] ? labelClash(overview.autopilots, editing?.id, draft.issue_repo || draft.repo, labels()) : null;
  const finished = (): AutopilotDraft => {
    const triggers = { ...draft.triggers };
    if (triggers["github-issue"]) triggers["github-issue"] = { labels: labels() };
    return { ...draft, triggers, name: draft.name.trim() || `${draft.repo.split("/")[1] ?? draft.repo} ${flowTitle(flow.name).toLowerCase()}` };
  };

  function toggleTrigger(kind: TriggerType, checked: boolean) {
    const triggers = { ...draft.triggers };
    if (checked) triggers[kind] = { labels: [] };
    else delete triggers[kind];
    setDraft({ ...draft, triggers });
  }

  async function save() {
    setBusy(true);
    setError("");
    try {
      if (editing) await autopilotApi.update(editing.id, finished());
      else await autopilotApi.create(finished());
      onSaved();
    } catch (e) {
      setError((e as Error).message);
      setBusy(false);
    }
  }

  async function remove() {
    if (!editing) return;
    setBusy(true);
    try {
      await autopilotApi.remove(editing.id);
      onSaved();
    } catch (e) {
      setError((e as Error).message);
      setBusy(false);
    }
  }

  const title = editing ? `Edit ${editing.name}` : `Set up ${flowTitle(flow.name)}`;

  return <Modal title={title} onClose={onClose} width={640}>
    {step === "triggers" && <>
      <div className="ap-row2">
      <label className="ap-field"><span>Workspace</span>
        <select style={inputStyle} value={draft.workspace} onChange={e => setDraft({ ...draft, workspace: e.target.value })}>
          <option value="">None</option>
          {!workspaces.includes(draft.workspace) && draft.workspace && <option value={draft.workspace}>{draft.workspace}</option>}
          {workspaces.map(w => <option key={w} value={w}>{w}</option>)}
        </select>
      </label>
      <label className="ap-field"><span>Code repository</span>
        {repos && repos.length > 0
          ? <select style={inputStyle} value={draft.repo} onChange={e => setDraft({ ...draft, repo: e.target.value })}>
            {!repos.includes(draft.repo) && <option value={draft.repo}>{draft.repo || "Choose a repository"}</option>}
            {repos.map(r => <option key={r} value={r}>{r}</option>)}
          </select>
          : <input style={inputStyle} value={draft.repo} placeholder={repos ? "owner/name" : "Loading repositories…"}
            onChange={e => setDraft({ ...draft, repo: e.target.value.trim() })} />}
      </label>
      </div>
      <div className="ap-label">Start a run on</div>
      <div className="ap-trigs">
        {flow.triggers.map(kind => {
          const available = overview.triggers[kind]?.available;
          const checked = !!draft.triggers[kind];
          return <div key={kind} className={`ap-trig${checked ? " ap-trig-on" : ""}${available ? "" : " ap-trig-off"}`}>
            <label>
              <input type="checkbox" checked={checked} disabled={!available} onChange={e => toggleTrigger(kind, e.target.checked)} />
              <span className="ap-glyph">{TRIGGERS[kind].glyph}</span>
              <span className="ap-trig-name">{TRIGGERS[kind].name}<small>{available ? TRIGGERS[kind].hint : "Coming soon"}</small></span>
            </label>
            {checked && kind === "github-issue" && <div className="ap-trig-fields">
              <label className="ap-field"><span>Issues from</span>
                {repos && repos.length > 0
                  ? <select style={inputStyle} value={draft.issue_repo} onChange={e => setDraft({ ...draft, issue_repo: e.target.value })}>
                    <option value="">The code repository</option>
                    {draft.issue_repo && !repos.includes(draft.issue_repo) && <option value={draft.issue_repo}>{draft.issue_repo}</option>}
                    {repos.filter(r => r !== draft.repo).map(r => <option key={r} value={r}>{r}</option>)}
                  </select>
                  : <input style={inputStyle} value={draft.issue_repo} placeholder="Leave empty for the code repository"
                    onChange={e => setDraft({ ...draft, issue_repo: e.target.value.trim() })} />}
              </label>
              <label className="ap-field"><span>Labels</span>
                <input style={inputStyle} value={labelsText} onChange={e => setLabelsText(e.target.value)} placeholder="Leave empty for every new issue" /></label>
              <p className="ap-muted ap-small">Issues with any of these labels, labelled from now on.</p>
              {clash && <p className="ap-warn">{clash.name} also watches {clash.repo} for “{clash.label}”, so both would start a run for the same issue.</p>}
            </div>}
          </div>;
        })}
      </div>
    </>}

    {step === "confirm" && <>
      <label className="ap-field"><span>Name</span>
        <input style={inputStyle} value={draft.name} placeholder={finished().name} onChange={e => setDraft({ ...draft, name: e.target.value })} /></label>
      <div className="ap-recap">
        {draft.repo}{draft.issue_repo && draft.issue_repo !== draft.repo ? <span className="ap-muted"> · issues from {draft.issue_repo}</span> : null}{draft.workspace ? <span className="ap-muted"> · in {draft.workspace}</span> : null}
        <ul>{(Object.keys(finished().triggers) as TriggerType[]).map(k => <li key={k}>{triggerLine(k, finished().triggers[k])}</li>)}</ul>
        <div className="ap-small ap-muted">Each run ends in a draft pull request. Nothing is merged.</div>
        <div className="ap-small ap-muted">Runs in Docker on this Mac, on your Claude subscription. Keep the Mac awake.</div>
        <div className="ap-small ap-muted ap-secrets">Issue text can steer what a run pushes, so keep secrets and write permissions out of {draft.repo || "repository"} workflows that run on <code>autopilot/**</code> branches.</div>
      </div>
      <details className="ap-limits">
        <summary>Limits <span>{draft.budget_m}M tokens a run · {draft.max_runs} at once</span></summary>
        <div className="ap-row2">
          <label className="ap-field"><span>Tokens per run (millions)</span>
            <input style={inputStyle} type="number" min={1} max={50} value={draft.budget_m}
              onChange={e => setDraft({ ...draft, budget_m: Math.max(1, Math.min(50, Number(e.target.value) || 1)) })} /></label>
          <label className="ap-field"><span>Runs at once</span>
            <select style={inputStyle} value={draft.max_runs} onChange={e => setDraft({ ...draft, max_runs: Number(e.target.value) })}>
              {[1, 2, 3, 4].map(n => <option key={n} value={n}>{n}</option>)}</select></label>
        </div>
      </details>
    </>}

    {error && <p className="ap-error-text" role="alert">{error}</p>}
    <div className="ap-foot">
      {editing && <span className="ap-foot-left"><Button variant="danger" size="sm" onClick={() => void remove()} disabled={busy}>Remove</Button></span>}
      {step === "triggers" && (!anyOn || !draft.repo) && <span className="ap-hint">{!draft.repo ? "Choose a repository" : "Turn on at least one trigger"}</span>}
      {step === "confirm"
        ? <Button variant="ghost" onClick={() => setStep("triggers")}>Back</Button>
        : <Button variant="ghost" onClick={onClose}>Cancel</Button>}
      {step === "triggers" && <Button onClick={() => setStep("confirm")} disabled={!anyOn || !draft.repo}>Next</Button>}
      {step === "confirm" && <Button onClick={() => void save()} disabled={busy}>{editing ? "Save" : "Turn on"}</Button>}
    </div>
  </Modal>;
}

// A workspace's Flows tab: the flows on autopilot in this workspace (and its
// subfolders) and their runs, one-off runs included. Managed on the Autopilot page.
export function WorkspaceFlows({ workspace }: { workspace: string }) {
  const [data, setData] = useState<AutopilotOverview | null>(null);
  const [error, setError] = useState("");
  const [openRun, setOpenRun] = useState<string | null>(null);
  useEffect(() => {
    let stopped = false;
    let timer: ReturnType<typeof setTimeout>;
    const tick = async () => {
      try {
        const next = await autopilotApi.overview(workspace);
        if (!stopped) { setData(next); setError(""); }
      } catch (e) {
        if (!stopped) setError((e as Error).message);
      }
      if (!stopped) timer = setTimeout(tick, POLL_MS);
    };
    void tick();
    return () => { stopped = true; clearTimeout(timer); };
  }, [workspace]);
  if (!data) return <div className="ap-page ap-embedded"><div className="ap-col"><p className="ap-muted">{error || "Loading…"}</p></div></div>;
  const flowsById = new Map(data.flows.map(f => [f.id, f]));
  return <div className="ap-page ap-embedded"><div className="ap-col">
    {error && <p className="ap-error" role="alert">{error}</p>}
    <section className="ap-sec">
      <h2>On autopilot</h2>
      {data.autopilots.length === 0
        ? <p className="ap-muted">None in this workspace. Set one up on the Autopilot page.</p>
        : <div className="ap-list">{data.autopilots.map(entry => <AutopilotRow key={entry.id} entry={entry} flowsById={flowsById} workspace={workspace} />)}</div>}
    </section>
    <section className="ap-sec">
      <h2>Runs</h2>
      {data.runs.length === 0
        ? <p className="ap-muted">No runs in this workspace yet.</p>
        : <RunTable runs={data.runs} openRun={openRun} setOpenRun={setOpenRun} />}
    </section>
  </div></div>;
}
