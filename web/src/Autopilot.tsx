import { useCallback, useEffect, useState } from "react";
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
  "github-issue": { name: "GitHub issues", glyph: "GH", hint: "From people with write access to the repository." },
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
      <i>{TRIGGERS[kind].glyph}</i>{triggerLine(kind, entry.triggers[kind])}
    </span>)}
  </div>;
}

export function AutopilotPage() {
  const [data, setData] = useState<AutopilotOverview | null>(null);
  const [error, setError] = useState("");
  const [setup, setSetup] = useState<{ editing: AutopilotEntry | null } | null>(null);
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

  if (!data) return <div className="ap-page"><h1>Autopilot</h1><p className="ap-muted">{error || "Loading…"}</p></div>;
  const on = data.autopilots.filter(a => a.on).length;
  const paused = data.autopilots.length - on;
  const flowsById = new Map(data.flows.map(f => [f.id, f]));

  return <div className="ap-page">
    <header className="ap-head">
      <div>
        <h1>Autopilot</h1>
        <p className="ap-muted">Flows run in sandboxes on this Mac (Docker), using your Claude subscription. Your Mac has to stay awake.</p>
      </div>
      <Button onClick={() => setSetup({ editing: null })}>Put a flow on autopilot</Button>
    </header>
    {error && <p className="ap-error" role="alert">{error}</p>}
    {data.problems.length > 0 && <p className="ap-warn">Runs can't start yet: {data.problems.join("; ")}.</p>}

    <h2 className="ap-sec">On autopilot <small>{on} on{paused ? ` · ${paused} paused` : ""}</small></h2>
    <div className="ap-cards">
      {data.autopilots.map(entry => <article key={entry.id} className={`ap-card${entry.on ? "" : " ap-off"}`}>
        <div className="ap-card-top">
          <h3>{entry.name}</h3>
          <span className={entry.on ? "ap-on" : "ap-paused"}>{entry.on ? "On" : "Paused"}</span>
        </div>
        <div className="ap-muted">{flowTitle(flowsById.get(entry.flow)?.name ?? entry.flow)} · <b>{entry.repo}</b>{entry.workspace ? <> · in {entry.workspace}</> : null}</div>
        <TriggerChips entry={entry} />
        <div className="ap-muted ap-small">
          {entry.watch.error ? <span className="ap-error-text">{entry.watch.error}</span>
            : entry.active ? `${entry.active} running` : entry.watch.checked_at ? `Checked ${ago(entry.watch.checked_at)}` : "Not checked yet"}
        </div>
        <div className="ap-card-actions">
          <Button size="sm" variant="ghost" onClick={() => setSetup({ editing: entry })}>Edit</Button>
          <Button size="sm" variant="ghost" onClick={() => setRunOnce(entry)}>Run once…</Button>
          <Button size="sm" variant="ghost" onClick={() => void toggle(entry)}>{entry.on ? "Pause" : "Resume"}</Button>
        </div>
      </article>)}
      <button type="button" className="ap-card ap-add" onClick={() => setSetup({ editing: null })}>
        <b>+ Put a flow on autopilot</b><span className="ap-muted">Let GitHub, Jira, Slack or a schedule start a flow</span>
      </button>
    </div>

    <div className="ap-counts">
      <div><strong className="ap-wait">{data.summary.waiting_gate}</strong>Needs you</div>
      <div><strong>{data.summary.active}</strong>Running</div>
      <div><strong>{data.summary.done_today}</strong>Done today</div>
      <div><strong className={data.summary.failed_today ? "ap-error-text" : ""}>{data.summary.failed_today}</strong>Failed today</div>
    </div>

    <h2 className="ap-sec">Runs <small>{data.runs.length}</small></h2>
    {data.runs.length === 0
      ? <p className="ap-muted">No runs yet. A run starts when a trigger fires, or from Run once on a card.</p>
      : <div className="ap-table-wrap"><table className="ap-table">
        <thead><tr><th>Issue</th><th>Autopilot</th><th>Step</th><th>State</th><th>Tokens</th><th>Started</th></tr></thead>
        <tbody>{data.runs.map(run => <RunRow key={run.id} run={run} open={openRun === run.id}
          onToggle={() => setOpenRun(id => id === run.id ? null : run.id)} />)}</tbody>
      </table></div>}

    {setup && <SetupDialog overview={data} editing={setup.editing} onClose={() => setSetup(null)}
      onSaved={() => { setSetup(null); void load(); }} />}
    {runOnce && <RunOnceDialog entry={runOnce} onClose={() => setRunOnce(null)}
      onStarted={() => { setRunOnce(null); void load(); }} />}
  </div>;
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
      <td><span className={`ap-state ap-state-${run.state}`}>{STATE_LABEL[run.state] ?? run.state}</span></td>
      <td className="ap-mono">{run.tokens != null ? `${Math.round(run.tokens / 1000)}k` : "—"}</td>
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
      <p className="ap-muted ap-small">
        Branch <code>{run.branch}</code> on this Mac{run.item.started_by ? ` · started by ${run.item.started_by}` : ""}
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
    <label className="ap-field"><span>Issue number in {entry.repo}</span>
      <input style={inputStyle} value={issue} onChange={e => setIssue(e.target.value)} placeholder="#42" autoFocus
        onKeyDown={e => { if (e.key === "Enter" && issue.trim()) void start(); }} /></label>
    {error && <p className="ap-error-text">{error}</p>}
    <div className="ap-foot">
      <Button variant="ghost" onClick={onClose}>Cancel</Button>
      <Button onClick={() => void start()} disabled={busy || !issue.trim()}>Start</Button>
    </div>
  </Modal>;
}

const emptyDraft = (flow: AutopilotFlow, repo: string, overview: AutopilotOverview): AutopilotDraft => {
  const first = flow.triggers.find(k => overview.triggers[k]?.available);
  return {
    flow: flow.id, name: "", workspace: "", repo,
    triggers: first ? { [first]: { labels: first === "github-issue" ? ["autopilot"] : [] } } : {},
    approvals: { spec_on_visible: false, keep_merge_approval: false }, budget_m: 5, max_runs: 2,
  };
};

function SetupDialog({ overview, editing, onClose, onSaved }: {
  overview: AutopilotOverview; editing: AutopilotEntry | null; onClose: () => void; onSaved: () => void;
}) {
  const [step, setStep] = useState(editing ? 2 : 1);
  const [draft, setDraft] = useState<AutopilotDraft | null>(editing ? {
    flow: editing.flow, name: editing.name, workspace: editing.workspace ?? "", repo: editing.repo, triggers: editing.triggers,
    approvals: editing.approvals, budget_m: editing.budget_m, max_runs: editing.max_runs,
  } : null);
  const [repos, setRepos] = useState<string[] | null>(null);
  const [workspaces, setWorkspaces] = useState<string[]>([]);
  const [labelsText, setLabelsText] = useState((editing?.triggers["github-issue"]?.labels ?? ["autopilot"]).join(", "));
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let stopped = false;
    autopilotApi.repositories().then(list => { if (!stopped) setRepos(list); }).catch(() => { if (!stopped) setRepos([]); });
    api.folders().then(r => { if (!stopped) setWorkspaces(r.folders); }).catch(() => undefined);
    return () => { stopped = true; };
  }, []);

  const flow = draft ? overview.flows.find(f => f.id === draft.flow) : undefined;
  const anyOn = !!draft && Object.keys(draft.triggers).length > 0;
  const labels = () => labelsText.split(",").map(s => s.trim()).filter(Boolean);
  const finished = (): AutopilotDraft => {
    const d = draft!;
    const triggers = { ...d.triggers };
    if (triggers["github-issue"]) triggers["github-issue"] = { labels: labels() };
    return { ...d, triggers, name: d.name.trim() || `${d.repo.split("/")[1] ?? d.repo} ${flowTitle(flow?.name ?? "").toLowerCase()}` };
  };

  function pick(f: AutopilotFlow) {
    setDraft(emptyDraft(f, draft?.repo ?? repos?.[0] ?? "", overview));
    setStep(2);
  }

  function toggleTrigger(kind: TriggerType, checked: boolean) {
    if (!draft) return;
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

  const title = editing ? `Edit ${editing.name}` : step === 1 || !flow ? "Put a flow on autopilot" : `Put ${flowTitle(flow.name)} on autopilot`;
  const steps = ["Flow", "Triggers", "Confirm"];

  return <Modal title={title} onClose={onClose} width={640}>
    {!editing && <div className="ap-wizsteps">{steps.map((n, i) => <span key={n} className={i + 1 === step ? "ap-on-step" : ""}>{n}</span>)}</div>}

    {step === 1 && <div className="ap-flowpick">
      {overview.flows.map(f => <button key={f.id} type="button" onClick={() => pick(f)}>
        <h3>{flowTitle(f.name)}</h3>
        <p>{f.description}</p>
        <div className="ap-chips">{f.triggers.map(k => <span key={k} className={`ap-chip${overview.triggers[k]?.available ? "" : " ap-chip-off"}`}>
          <i>{TRIGGERS[k].glyph}</i>{TRIGGERS[k].name}{overview.triggers[k]?.available ? "" : " (soon)"}
        </span>)}</div>
      </button>)}
      {overview.flows.length === 0 && <p className="ap-muted">No flows declare triggers yet.</p>}
    </div>}

    {step === 2 && draft && flow && <>
      <div className="ap-row2">
      <label className="ap-field"><span>Workspace</span>
        <select style={inputStyle} value={draft.workspace} onChange={e => setDraft({ ...draft, workspace: e.target.value })}>
          <option value="">None</option>
          {!workspaces.includes(draft.workspace) && draft.workspace && <option value={draft.workspace}>{draft.workspace}</option>}
          {workspaces.map(w => <option key={w} value={w}>{w}</option>)}
        </select>
      </label>
      <label className="ap-field"><span>Repository</span>
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
              <label className="ap-field"><span>Labels</span>
                <input style={inputStyle} value={labelsText} onChange={e => setLabelsText(e.target.value)} placeholder="Leave empty for every new issue" /></label>
              <p className="ap-muted ap-small">Issues with any of these labels, labelled from now on.</p>
            </div>}
          </div>;
        })}
      </div>
    </>}

    {step === 3 && draft && flow && <>
      <label className="ap-field"><span>Name</span>
        <input style={inputStyle} value={draft.name} placeholder={finished().name} onChange={e => setDraft({ ...draft, name: e.target.value })} /></label>
      <div className="ap-recap">
        {draft.repo}{draft.workspace ? <span className="ap-muted"> · in {draft.workspace}</span> : null}
        <ul>{(Object.keys(finished().triggers) as TriggerType[]).map(k => <li key={k}>{triggerLine(k, finished().triggers[k])}</li>)}</ul>
        <div className="ap-small ap-muted">Each run commits to its own branch on this Mac. Nothing is pushed or merged yet. Pause any time from its card.</div>
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
      {step === 2 && (!anyOn || !draft?.repo) && <span className="ap-hint">{!draft?.repo ? "Choose a repository" : "Turn on at least one trigger"}</span>}
      {step > (editing ? 2 : 1)
        ? <Button variant="ghost" onClick={() => setStep(step - 1)}>Back</Button>
        : <Button variant="ghost" onClick={onClose}>Cancel</Button>}
      {step === 2 && <Button onClick={() => setStep(3)} disabled={!anyOn || !draft?.repo}>Next</Button>}
      {step === 3 && <Button onClick={() => void save()} disabled={busy}>{editing ? "Save" : "Put on autopilot"}</Button>}
    </div>
  </Modal>;
}
