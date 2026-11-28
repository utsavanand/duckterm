import { ReactNode, useCallback, useEffect, useState } from "react";
import {
  AutopilotAttempt, AutopilotChanges, AutopilotDraft, AutopilotEntry, AutopilotFlow, AutopilotOverview, AutopilotRun,
  AutopilotRunDetail, AutopilotSummary, autopilotApi, Classification, TriggerType,
} from "./autopilotApi";
import { modelHtml } from "./render";
import { api } from "./api";
import { approvalSigner } from "./desktop";
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
  merged: "Merged", ended: "Ended", rejected: "Rejected", needs_info: "Needs info", declined: "Declined", failed: "Failed",
  cancelled: "Cancelled", interrupted: "Interrupted",
};

const RULE_LABEL = { security: "Security", data: "Data", ai: "AI behaviour" } as const;
const RULE_HINT = { security: "src/auth/*\n.github/workflows/*", data: "migrations/*", ai: "prompts/*" } as const;

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
  const [openRun, setOpenRun] = useState<OpenRun | null>(null);

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
  if (openRun) return <div className="ap-page"><div className="ap-col">
    <RunDetail id={openRun.id} tab={openRun.tab} back="Autopilot" onBack={() => setOpenRun(null)} />
  </div></div>;
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
    <Waiting runs={data.runs} onAnswered={load} onOpen={setOpenRun} />

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
        : <RunTable runs={data.runs} onOpen={id => setOpenRun({ id })} />}
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
  const merges = { shadow: `asks before merging (${entry.shadow_done ?? 0} of 10 so far)`, on: "merges when checks pass", off: "asks before every merge" }[entry.auto_merge ?? "shadow"];
  return <div className="ap-item">
    <div className="ap-item-main">
      <h3>{entry.name} <span className={entry.on ? "ap-on" : "ap-paused"}>{entry.on ? "On" : "Paused"}</span></h3>
      <div className="ap-meta">{flowTitle(flowsById.get(entry.flow)?.name ?? entry.flow)} · <b>{entry.repo}</b>{issueRepo(entry) !== entry.repo && <> · issues from <b>{issueRepo(entry)}</b></>}{where} · {status} · {merges}</div>
      <TriggerChips entry={entry} />
    </div>
    {children && <div className="ap-acts">{children}</div>}
  </div>;
}

function RunTable({ runs, onOpen }: { runs: AutopilotRun[]; onOpen: (id: string) => void }) {
  return <div className="ap-table-wrap"><table className="ap-table">
    <thead><tr><th>Issue</th><th>Autopilot</th><th>Step</th><th>State</th><th title="Spec · Merge">Approvals</th><th className="ap-num-col">Tokens</th><th>Started</th></tr></thead>
    <tbody>{runs.map(run => <RunRow key={run.id} run={run} onOpen={() => onOpen(run.id)} />)}</tbody>
  </table></div>;
}

type OpenRun = { id: string; tab?: RunTab };

// Runs stopped at a gate, each with why the policy asked and what it asks
// about. The spec itself is read on the run's Spec tab.
function Waiting({ runs, onAnswered, onOpen }: {
  runs: AutopilotRun[]; onAnswered: () => void | Promise<void>; onOpen: (open: OpenRun) => void;
}) {
  const waiting = runs.filter(r => r.state === "waiting_gate" && r.gate);
  if (waiting.length === 0) return null;
  return <section className="ap-sec">
    <h2>Needs you <small>{waiting.length}</small></h2>
    <div className="ap-gates">{waiting.map(run => <GateCard key={run.id} run={run} onAnswered={onAnswered} onOpen={onOpen} />)}</div>
  </section>;
}

function GateCard({ run, onAnswered, onOpen }: {
  run: AutopilotRun; onAnswered: () => void | Promise<void>; onOpen: (open: OpenRun) => void;
}) {
  const gate = run.gate!;
  const spec = gate.step === "spec-gate";
  const merge = gate.step === "merge-gate";
  return <article className="ap-gate">
    <div className="ap-gate-kind">{spec ? "Spec approval" : merge ? <>Merge approval{run.pr ? ` · PR #${run.pr.number}` : ""}{gate.shadow ? " · shadow mode" : ""}</> : gate.question || gate.step}</div>
    <h3><button type="button" className="ap-gate-title" onClick={() => onOpen({ id: run.id })}>
      {run.item.number ? <span className="ap-num">#{run.item.number}</span> : null} {run.item.title ?? run.id}
    </button></h3>
    <RiskChips c={run.classification} />
    <Why run={run} />
    {merge
      ? <MergeActions run={run} onAnswered={onAnswered} onChanges={() => onOpen({ id: run.id, tab: "changes" })} />
      : <GateActions run={run} onAnswered={onAnswered} onRead={spec ? () => onOpen({ id: run.id, tab: "spec" }) : undefined} />}
  </article>;
}

function Why({ run }: { run: AutopilotRun }) {
  const gate = run.gate!;
  const ci = run.ci === "none" ? "This repository has no CI." : gate.head ? <>CI green on <code>{gate.head.slice(0, 7)}</code>.</> : null;
  if (gate.shadow) return <p className="ap-why">
    The policy would <b>auto-merge</b> this. Shadow mode stops it for you (run {gate.shadow_run} of 10). {ci}
  </p>;
  return <p className="ap-why">
    {gate.why ? <>Required because: <b>{gate.why}</b>. </> : null}
    {gate.step === "spec-gate" ? "No code written yet; the sandbox is stopped." : null}
    {gate.step === "merge-gate" ? ci : null}
  </p>;
}

// A merge is approved only with Touch ID, signed by the Mac app; a browser
// has no signer, so it can reject but not approve.
function MergeActions({ run, onAnswered, onChanges }: {
  run: AutopilotRun; onAnswered: () => void | Promise<void>; onChanges?: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const signer = approvalSigner();
  const gate = run.gate!;
  async function act(work: () => Promise<unknown>) {
    setBusy(true);
    setError("");
    try {
      await work();
      await onAnswered();
    } catch (e) {
      setError((e as Error).message);
      setBusy(false);
    }
  }
  const approve = () => act(async () => {
    const { payload } = await autopilotApi.mergeChallenge(run.id);
    await autopilotApi.mergeApprove(run.id, payload, await signer!.sign(payload));
  });
  const minutes = gate.asked_at ? Math.max(0, Math.round((Date.now() - gate.asked_at) / 60000)) : null;
  return <>
    {error && <p className="ap-error-text">{error}</p>}
    <div className="ap-gate-foot">
      {run.pr && <a className="ap-btn-link" href={run.pr.url} target="_blank" rel="noreferrer">Open PR</a>}
      {onChanges && <Button size="sm" variant="ghost" onClick={onChanges}>View changes</Button>}
      <Button size="sm" variant="ghost" disabled={busy} onClick={() => void act(() => autopilotApi.answer(run.id, gate.step, "reject"))}>Reject</Button>
      {signer
        ? <Button size="sm" disabled={busy} onClick={() => void approve()}>Approve with Touch ID</Button>
        : <span className="ap-muted ap-small">Approve with Touch ID in the DuckTerm app</span>}
      {minutes != null && <span className="ap-age">waiting {minutes < 60 ? `${minutes} min` : `${Math.floor(minutes / 60)} h`}</span>}
    </div>
  </>;
}

// The answers a waiting gate takes. A spec sent back carries a note.
function GateActions({ run, onAnswered, onRead }: {
  run: AutopilotRun; onAnswered: () => void | Promise<void>; onRead?: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [rejecting, setRejecting] = useState(false);
  const gate = run.gate!;
  async function answer(choice: string, note?: string) {
    setBusy(true);
    setError("");
    try {
      await autopilotApi.answer(run.id, gate.step, choice, note);
      setRejecting(false);
      await onAnswered();
    } catch (e) {
      setError((e as Error).message);
      setBusy(false);
    }
  }
  const minutes = gate.asked_at ? Math.max(0, Math.round((Date.now() - gate.asked_at) / 60000)) : null;
  return <>
    {error && <p className="ap-error-text">{error}</p>}
    <div className="ap-gate-foot">
      {onRead && <Button size="sm" variant="ghost" onClick={onRead}>Read spec</Button>}
      {gate.choices.includes("revise")
        ? <Button size="sm" variant="ghost" disabled={busy} onClick={() => setRejecting(true)}>Reject with note</Button>
        : gate.choices.includes("reject") && <Button size="sm" variant="ghost" disabled={busy} onClick={() => void answer("reject")}>Reject</Button>}
      {gate.choices.includes("approve") && <Button size="sm" disabled={busy} onClick={() => void answer("approve")}>Approve</Button>}
      {minutes != null && <span className="ap-age">waiting {minutes < 60 ? `${minutes} min` : `${Math.floor(minutes / 60)} h`}</span>}
    </div>
    {rejecting && <RejectDialog busy={busy} error={error} canEnd={gate.choices.includes("reject")} onClose={() => setRejecting(false)}
      onRevise={note => void answer("revise", note)} onEnd={() => void answer("reject")} />}
  </>;
}

function RejectDialog({ busy, error, canEnd, onClose, onRevise, onEnd }: {
  busy: boolean; error: string; canEnd: boolean; onClose: () => void; onRevise: (note: string) => void; onEnd: () => void;
}) {
  const [note, setNote] = useState("");
  return <Modal title="Reject the spec" onClose={onClose}>
    <p className="ap-muted ap-lead">The spec step runs again with your note, at most 2 more times.</p>
    <label className="ap-field"><span>Note for the spec step</span>
      <textarea style={{ ...inputStyle, fontFamily: "inherit", minHeight: 96, resize: "vertical" }} value={note} onChange={e => setNote(e.target.value)}
        placeholder="What the spec should change" /></label>
    {error && <p className="ap-error-text">{error}</p>}
    <div className="ap-foot">
      {canEnd && <span className="ap-foot-left"><Button variant="danger" size="sm" disabled={busy} onClick={onEnd}>End the run instead</Button></span>}
      <Button variant="ghost" onClick={onClose}>Cancel</Button>
      <Button disabled={busy || !note.trim()} onClick={() => onRevise(note.trim())}>Send back</Button>
    </div>
  </Modal>;
}

// The classification as chips: what the fix touches, with risk in colour.
function RiskChips({ c }: { c?: AutopilotRun["classification"] }) {
  if (!c) return null;
  const chips: [string, string][] = (c.surfaces ?? []).map(s => [s, ""]);
  if (c.user_visible) chips.push(["user visible", ""]);
  if (c.security?.level && c.security.level !== "none") chips.push(["security", "ap-risk-sec"]);
  if (c.ai && c.ai !== "deterministic") chips.push(["AI behaviour", "ap-risk-ai"]);
  if (c.data && c.data !== "none") chips.push([`data: ${c.data}`, "ap-risk-amber"]);
  if (c.regression?.level && c.regression.level !== "none") chips.push([`regression: ${c.regression.level}`, "ap-risk-amber"]);
  if (chips.length === (c.surfaces ?? []).length) chips.push(["routine", ""]);
  return <div className="ap-chips">{chips.map(([label, cls]) => <span key={label} className={`ap-chip ${cls}`}>{label}</span>)}</div>;
}

function RunRow({ run, onOpen }: { run: AutopilotRun; onOpen: () => void }) {
  const live = run.state === "running" || run.state === "starting";
  const step = live ? (run.stage === "ci" || run.stage === "merging" ? run.stage : run.current) ?? "starting" : "—";
  return <tr className="ap-row" onClick={onOpen} tabIndex={0}
    onKeyDown={e => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onOpen(); } }}>
    <td>{run.item.number ? <span className="ap-num">#{run.item.number}</span> : null} {run.item.title ?? run.id}
      <RiskChips c={run.classification} /></td>
    <td className="ap-muted">{run.autopilot_name}{run.workspace ? <span className="ap-small"> · {run.workspace}</span> : null}</td>
    <td>{step}</td>
    <td><RunState run={run} /></td>
    <td><GateMarks run={run} /></td>
    <td className="ap-mono ap-num-col">{tokens(run.tokens)}</td>
    <td className="ap-muted">{new Date(run.started_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}</td>
  </tr>;
}

const STAGE_LABEL: Record<string, string> = { ci: "Waiting for CI", "fixing-ci": "Fixing CI", merging: "Merging" };

function RunState({ run }: { run: AutopilotRun }) {
  const label = run.state === "running" && run.stage && STAGE_LABEL[run.stage] ? STAGE_LABEL[run.stage]
    : run.pr && run.state === "succeeded" ? "PR open" : STATE_LABEL[run.state] ?? run.state;
  return <span className={`ap-state ap-state-${run.state}`}>{label}</span>;
}

type GateMark = "skip" | "req" | "wait" | "ok" | "rej" | "none";

// Where a person approves a run: the spec (S) and the merge (M).
function gateMarks(run: AutopilotRun): { S: GateMark; M: GateMark } {
  const steps = run.steps ?? [];
  const spec = steps.filter(s => s.step === "spec-gate").at(-1);
  const policy = steps.filter(s => s.step === "spec-policy").at(-1);
  const S: GateMark = run.gate?.step === "spec-gate" ? "wait"
    : spec?.choice === "approve" ? "ok" : spec ? "rej"
    : policy?.choice === "skip" ? "skip" : "none";
  const merge = (run.answers ?? []).filter(a => a.step === "merge-gate").at(-1);
  const M: GateMark = run.gate?.step === "merge-gate" ? "wait"
    : merge?.answer === "approve" ? "ok" : merge ? "rej"
    : run.state === "merged" ? "skip" : run.merge_reasons?.length ? "req" : "none";
  return { S, M };
}

const MARK_TITLE: Record<GateMark, string> = {
  skip: "not required", req: "required", wait: "waiting for you", ok: "approved", rej: "rejected", none: "not decided yet",
};

function GateMarks({ run }: { run: AutopilotRun }) {
  const marks = gateMarks(run);
  return <span className="ap-gate-marks">{(["S", "M"] as const).map(k =>
    <span key={k} className={`ap-g ap-g-${marks[k]}`} title={`${k === "S" ? "spec" : "merge"}: ${MARK_TITLE[marks[k]]}`}>{k}</span>)}</span>;
}

const tokens = (n: number | undefined) => n != null ? `${Math.round(n / 1000)}k` : "—";

function duration(seconds: number): string {
  const s = Math.max(0, Math.round(seconds));
  if (s < 60) return `${s} s`;
  const m = Math.floor(s / 60);
  if (m < 60) return s % 60 ? `${m} min ${s % 60} s` : `${m} min`;
  return `${Math.floor(m / 60)} h ${m % 60} min`;
}

const HARNESS_LABEL: Record<string, string> = { "claude-code": "Claude Code", codex: "Codex" };

// What ran a step: the harness for model steps, a script, or the owner.
function kindLabel(a: AutopilotAttempt): string {
  if (a.kind === "gate") return "your approval";
  if (a.kind === "exec") return "script";
  if (a.kind === "parallel") return "runs the reviews together";
  const who = HARNESS_LABEL[a.harness ?? ""] ?? a.harness ?? "Claude Code";
  return a.kind === "decide" ? `${who} · decides` : who;
}

function attemptStatus(a: AutopilotAttempt): "done" | "now" | "fail" {
  if (a.status === "started") return "now";
  return a.exit ? "fail" : "done";
}

type RunTab = "steps" | "spec" | "changes" | "gates" | "issue";

const CI_LABEL: Record<string, string> = { pass: "Passed", fail: "Failed", timeout: "Timed out", none: "No CI here" };

// A run opened from the runs list: what each step did, the spec it wrote,
// the change on its branch and the issue it started from.
function RunDetail({ id, tab: firstTab, back, onBack }: { id: string; tab?: RunTab; back: string; onBack: () => void }) {
  const [run, setRun] = useState<AutopilotRunDetail | null>(null);
  const [error, setError] = useState("");
  const [tab, setTab] = useState<RunTab>(firstTab ?? "steps");
  const [reload, setReload] = useState(0);
  const [picked, setPicked] = useState<number | null>(null);

  useEffect(() => {
    let stopped = false;
    let timer: ReturnType<typeof setTimeout>;
    const tick = async () => {
      try {
        const next = await autopilotApi.run(id);
        if (stopped) return;
        setRun(next);
        setError("");
        if (["starting", "running", "waiting_gate"].includes(next.state)) timer = setTimeout(tick, POLL_MS);
      } catch (e) {
        if (!stopped) setError((e as Error).message);
      }
    };
    void tick();
    return () => { stopped = true; clearTimeout(timer); };
  }, [id, reload]);

  const crumb = <button type="button" className="ap-link ap-back" onClick={onBack}>← {back}</button>;
  if (!run) return <div className="ap-run">{crumb}<p className={error ? "ap-error" : "ap-muted"}>{error || "Loading…"}</p></div>;

  const ended = run.ended_at ?? (["starting", "running", "waiting_gate"].includes(run.state) ? Date.now() : run.started_at);
  const files = run.changes?.files ?? [];
  const shown = picked ?? run.attempts.length - 1;
  const attempt = run.attempts[shown];

  return <div className="ap-run">
    {crumb}
    <header className="ap-run-head">
      <h1>{run.item.number ? <span className="ap-num">#{run.item.number}</span> : null} {run.item.title ?? run.id}</h1>
      <div className="ap-meta">
        <RunState run={run} /> · {run.autopilot_name} · <b>{run.repo}</b> · started {new Date(run.started_at).toLocaleString([], { dateStyle: "medium", timeStyle: "short" })}
        {run.item.started_by ? ` by ${run.item.started_by}` : ""}
      </div>
      <div className="ap-meta">
        {run.pr && <><a href={run.pr.url} target="_blank" rel="noreferrer">Pull request #{run.pr.number}</a> into {run.pr.base} · </>}
        {run.item.url && <><a href={run.item.url} target="_blank" rel="noreferrer">Issue{run.item.number ? ` #${run.item.number}` : ""}</a> · </>}
        Branch <code>{run.branch}</code> {run.pushed ? "on GitHub" : "on this Mac"}
      </div>
      <RiskChips c={run.classification} />
    </header>

    {run.error && <p className="ap-error" role="alert">{run.error}</p>}
    {run.outcome && <p className="ap-warn">{run.state === "declined" ? "Declined at triage" : "The issue needs more information"}: {run.outcome}</p>}
    {run.pr_error && <p className="ap-error">Could not open the pull request: {run.pr_error}</p>}
    {run.pr_skipped && <p className="ap-warn">No pull request: {run.pr_skipped}.</p>}
    {run.gate && <section className="ap-gate">
      <div className="ap-gate-kind">{run.gate.step === "spec-gate" ? "Spec approval" : run.gate.step === "merge-gate" ? `Merge approval${run.gate.shadow ? " · shadow mode" : ""}` : "Waiting for you"}</div>
      <h3>{run.gate.question || run.gate.step}</h3>
      <Why run={run} />
      {run.gate.step === "merge-gate"
        ? <MergeActions run={run} onAnswered={() => setReload(n => n + 1)} onChanges={tab !== "changes" ? () => setTab("changes") : undefined} />
        : <GateActions run={run} onAnswered={() => setReload(n => n + 1)}
          onRead={run.gate.step === "spec-gate" && tab !== "spec" ? () => setTab("spec") : undefined} />}
    </section>}

    <dl className="ap-facts">
      <div><dt>Duration</dt><dd>{duration((ended - run.started_at) / 1000)}</dd></div>
      <div><dt>Tokens</dt><dd className="ap-mono-num">{tokens(run.tokens)}</dd></div>
      <div><dt>CI</dt><dd>{CI_LABEL[run.ci ?? ""] ?? (run.pr ? "Not checked yet" : "No pull request")}{run.ci_fixes ? ` · fixed ${run.ci_fixes}×` : ""}</dd></div>
      <div><dt>Sandbox</dt><dd>Docker on this Mac{run.ended_at ? ", removed" : ""}</dd></div>
    </dl>

    <div className="ap-tabs" role="tablist">
      {([["steps", "Steps", run.attempts.length], ["spec", "Spec", null], ["changes", "Changes", files.length], ["gates", "Gates", null], ["issue", "Issue", null]] as const)
        .map(([key, label, count]) => <button key={key} type="button" role="tab" aria-selected={tab === key}
          className={tab === key ? "ap-tab-on" : ""} onClick={() => setTab(key)}>
          {label}{count ? <span className="ap-tab-count">{count}</span> : null}
        </button>)}
    </div>

    {tab === "steps" && (run.attempts.length === 0
      ? <p className="ap-muted">No step has started yet.</p>
      : <div className="ap-split">
        <ol className="ap-attempts">{run.attempts.map((a, i) => <li key={`${a.step}-${a.attempt}`}>
          <button type="button" className={i === shown ? "ap-sel" : ""} onClick={() => setPicked(i)}>
            <i className={`ap-ic ap-ic-${attemptStatus(a)}`} />
            <span className="ap-att-main">
              <span className="ap-att-name">{a.step}{a.attempt > 1 ? <span className="ap-att-n">attempt {a.attempt}</span> : null}
                <span className="ap-att-kind">{kindLabel(a)}</span></span>
              <span className={`ap-att-out${a.exit ? " ap-att-bad" : ""}`}>{firstLine(a)}</span>
            </span>
            <span className="ap-att-time">{a.started_at ? duration((a.finished_at ?? Date.now() / 1000) - a.started_at) : ""}</span>
          </button>
        </li>)}</ol>
        {attempt && <section className="ap-panel">
          <h3>{attempt.step}{attempt.attempt > 1 ? ` · attempt ${attempt.attempt}` : ""}</h3>
          <div className="ap-meta">
            {kindLabel(attempt)}
            {attempt.started_at ? ` · ${new Date(attempt.started_at * 1000).toLocaleTimeString()}` : ""}
            {attempt.started_at ? ` · ${duration((attempt.finished_at ?? Date.now() / 1000) - attempt.started_at)}` : ""}
            {attempt.choice ? ` · chose ${attempt.choice}` : ""}
            {attempt.exit ? ` · exit ${attempt.exit}` : ""}
            {run.loops[attempt.step] ? ` · looped back ${run.loops[attempt.step]}×` : ""}
          </div>
          {attempt.reason
            ? <div className="ap-doc" dangerouslySetInnerHTML={{ __html: modelHtml(attempt.reason) }} />
            : <p className="ap-muted">{attempt.status === "started" ? "Still running." : "This step left no summary."}</p>}
        </section>}
      </div>)}

    {tab === "spec" && (run.spec
      ? <div className="ap-doc ap-doc-box" dangerouslySetInnerHTML={{ __html: modelHtml(run.spec) }} />
      : <p className="ap-muted">This run wrote no spec.</p>)}

    {tab === "changes" && <Changes changes={run.changes} />}

    {tab === "gates" && <GatesTab run={run} />}
    {tab === "issue" && <div className="ap-doc-box">
      <h3>{run.item.title ?? "Untitled"}</h3>
      {run.issue_body
        ? <div className="ap-doc" dangerouslySetInnerHTML={{ __html: modelHtml(run.issue_body) }} />
        : <p className="ap-muted">The issue has no description.</p>}
      {run.item.url && <p className="ap-small"><a href={run.item.url} target="_blank" rel="noreferrer">Open on GitHub</a></p>}
    </div>}
  </div>;
}

const CLASS_ROWS: [string, (c: Classification) => string][] = [
  ["surfaces", c => (c.surfaces ?? []).join(", ") || "—"],
  ["user visible", c => c.user_visible == null ? "—" : c.user_visible ? "yes" : "no"],
  ["regression", c => [c.regression?.level, (c.regression?.features ?? []).join(", ")].filter(Boolean).join(": ") || "—"],
  ["security", c => [c.security?.level, (c.security?.areas ?? []).join(", ")].filter(Boolean).join(": ") || "—"],
  ["AI", c => (c.ai ?? "—").replace("_", " ")],
  ["data", c => c.data ?? "—"],
  ["size", c => c.size ?? "—"],
];
const RULE_FIELD: Record<string, string> = { security: "security", data: "data", AI: "ai" };

// What the policy read and what it decided: the spec's classification, the
// security review's reading of the diff, and the path rules that matched.
function GatesTab({ run }: { run: AutopilotRunDetail }) {
  if (!run.spec_class) return <p className="ap-muted">No classification yet: the run has not written its spec.</p>;
  const specGate = run.attempts.filter(a => a.step === "spec-policy").at(-1);
  const hits = run.path_hits ?? [];
  return <div className="ap-changes">
    <div className="ap-table-wrap"><table className="ap-table">
      <thead><tr><th>Field</th><th>Spec</th><th>Security review (diff)</th><th>Path rules</th></tr></thead>
      <tbody>{CLASS_ROWS.map(([name, show]) => {
        const field = RULE_FIELD[name];
        const matched = field ? hits.filter(h => h.field === field).map(h => h.path) : [];
        const spec = show(run.spec_class!);
        const diff = run.diff_class ? show(run.diff_class) : "not reviewed yet";
        return <tr key={name}><td>{name}</td><td>{spec}</td>
          <td className={run.diff_class && diff !== spec ? "ap-higher" : ""}>{diff}</td>
          <td>{matched.length ? matched.map(p => <code key={p}>{p}</code>) : field ? "none" : "—"}</td></tr>;
      })}</tbody>
    </table></div>
    <div className="ap-row2">
      <div className="ap-panel"><h3>Spec approval</h3><p className="ap-muted">{specGate ? specGate.reason : "Not decided yet."}</p></div>
      <div className="ap-panel"><h3>Merge approval</h3><p className="ap-muted">{run.merge_reasons == null ? "Decided after CI passes."
        : run.merge_reasons.length ? `Required: ${run.merge_reasons.join("; ")}.` : "No rule requires it."}</p></div>
    </div>
    <p className="ap-muted ap-small">A script, not a model, decides these from the columns above and can only add an approval, never remove one. The issue text never reaches the path rules.</p>
  </div>;
}

function firstLine(a: AutopilotAttempt): string {
  if (a.status === "started") return "running…";
  const line = (a.reason ?? "").split("\n").find(l => l.trim()) ?? "";
  // markdown marks only: underscores inside names (CLOSE_TIMEOUT) are text
  return line.replace(/[*`#]|(^|\s)_+|_+(?=\s|$)/g, "$1").trim() || (a.choice ? `chose ${a.choice}` : "");
}

function Changes({ changes }: { changes: AutopilotChanges | null }) {
  if (!changes) return <p className="ap-muted">The run's clone is gone, so its change can't be shown.</p>;
  if (changes.files.length === 0) return <p className="ap-muted">The run changed no files.</p>;
  const added = changes.files.reduce((n, f) => n + f.added, 0);
  const removed = changes.files.reduce((n, f) => n + f.removed, 0);
  const patches = changes.patch.split(/^(?=diff --git )/m).filter(Boolean);
  return <div className="ap-changes">
    <div className="ap-table-wrap"><table className="ap-table">
      <thead><tr><th>{changes.files.length} files</th><th className="ap-num-col"><span className="ap-add">+{added}</span> <span className="ap-del">−{removed}</span></th></tr></thead>
      <tbody>{changes.files.map(f => <tr key={f.path}>
        <td className="ap-mono">{f.path}</td>
        <td className="ap-mono ap-num-col">{f.binary ? "binary" : <><span className="ap-add">+{f.added}</span> <span className="ap-del">−{f.removed}</span></>}</td>
      </tr>)}</tbody>
    </table></div>
    {patches.map(p => {
      const lines = p.replace(/\n$/, "").split("\n");
      const path = /^diff --git a\/(.*) b\//.exec(lines[0])?.[1] ?? lines[0];
      return <div key={path} className="ap-diff">
        <header className="ap-mono">{path}</header>
        <pre>{lines.slice(1).filter(l => !/^(index |--- |\+\+\+ |new file|deleted file)/.test(l)).map((l, i) =>
          <span key={i} className={l.startsWith("@@") ? "ap-dh" : l.startsWith("+") ? "ap-da" : l.startsWith("-") ? "ap-dd" : undefined}>{l || " "}</span>)}</pre>
      </div>;
    })}
    {changes.cut && <p className="ap-muted">The diff is cut at 400 KB. The branch has the rest.</p>}
  </div>;
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
    approvals: { spec_on_visible: false, keep_merge_approval: false }, budget_m: 5, max_runs: 2, test_command: "",
    auto_merge: "shadow", rules: { security: [], data: [], ai: [] },
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
    test_command: editing.test_command ?? "",
    auto_merge: editing.auto_merge ?? "shadow", rules: editing.rules ?? { security: [], data: [], ai: [] },
  } : emptyDraft(flow, overview));
  const [repos, setRepos] = useState<string[] | null>(null);
  const [workspaces, setWorkspaces] = useState<string[]>([]);
  const [labelsText, setLabelsText] = useState((editing?.triggers["github-issue"]?.labels ?? ["autopilot"]).join(", "));
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  // Open issues that the edited labels or issue repository newly match.
  const [backlog, setBacklog] = useState<number[]>([]);
  const [runBacklog, setRunBacklog] = useState(false);

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
    const rules = Object.fromEntries(Object.entries(draft.rules).map(([k, v]) => [k, v.map(x => x.trim()).filter(Boolean)])) as AutopilotDraft["rules"];
    return { ...draft, triggers, rules, name: draft.name.trim() || `${draft.repo.split("/")[1] ?? draft.repo} ${flowTitle(flow.name).toLowerCase()}` };
  };

  function toggleTrigger(kind: TriggerType, checked: boolean) {
    const triggers = { ...draft.triggers };
    if (checked) triggers[kind] = { labels: [] };
    else delete triggers[kind];
    setDraft({ ...draft, triggers });
  }

  async function next() {
    const watched = finished().triggers["github-issue"];
    const before = editing?.triggers["github-issue"];
    const moved = editing && watched && (!before || issueRepo(editing) !== (draft.issue_repo || draft.repo)
      || before.labels.join("\n") !== watched.labels.join("\n"));
    setError("");
    if (moved) {
      setBusy(true);
      try {
        setBacklog((await autopilotApi.backlog(editing.id, finished())).issues);
      } catch (e) {
        setError((e as Error).message);
        setBusy(false);
        return;
      }
      setBusy(false);
    } else setBacklog([]);
    setRunBacklog(false);
    setStep("confirm");
  }

  async function save() {
    setBusy(true);
    setError("");
    try {
      if (editing) await autopilotApi.update(editing.id, { ...finished(), backlog: runBacklog ? "run" : "new" });
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
        <div className="ap-small ap-muted">Each run opens a pull request and merges it after CI passes, when the approvals below allow.</div>
        <div className="ap-small ap-muted">Runs in Docker on this Mac, on your Claude subscription. Keep the Mac awake.</div>
        <div className="ap-small ap-muted ap-secrets">Issue text can steer what a run pushes, so keep secrets and write permissions out of {draft.repo || "repository"} workflows that run on <code>autopilot/**</code> branches.</div>
      </div>
      <p className="ap-muted ap-small ap-lead">You approve risky specs, and every merge for the first 10 runs.</p>
      <label className="ap-opt"><input type="checkbox" checked={draft.approvals.spec_on_visible}
        onChange={e => setDraft({ ...draft, approvals: { ...draft.approvals, spec_on_visible: e.target.checked } })} />Also approve the spec for any visible change</label>
      <label className="ap-opt"><input type="checkbox" checked={draft.auto_merge === "off"}
        onChange={e => setDraft({ ...draft, auto_merge: e.target.checked ? "off" : "shadow" })} />Keep approving every merge after the first 10</label>
      {(editing?.shadow_done ?? 0) >= 10 && draft.auto_merge !== "off" && <label className="ap-opt"><input type="checkbox" checked={draft.auto_merge === "on"}
        onChange={e => setDraft({ ...draft, auto_merge: e.target.checked ? "on" : "shadow" })} />Merge without asking when CI passes and no rule needs you</label>}
      {backlog.length > 0 && <fieldset className="ap-backlog">
        <legend>{backlog.length === 1 ? "1 open issue" : `${backlog.length} open issues`} in {draft.issue_repo || draft.repo} {backlog.length === 1 ? "matches" : "match"} the new settings</legend>
        <label><input type="radio" name="ap-backlog" checked={!runBacklog} onChange={() => setRunBacklog(false)} /> Only new ones<small>Run issues labelled from now on</small></label>
        <label><input type="radio" name="ap-backlog" checked={runBacklog} onChange={() => setRunBacklog(true)} /> Run them<small>{backlog.map(n => `#${n}`).join(", ")}, {draft.max_runs} at a time, if a person with write access labelled them</small></label>
      </fieldset>}
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
        <label className="ap-field"><span>Test command</span>
          <input style={inputStyle} value={draft.test_command} placeholder="e.g. pytest -q tests/unit"
            onChange={e => setDraft({ ...draft, test_command: e.target.value })} /></label>
        <p className="ap-muted ap-small">Run after each build; its exit code decides whether tests passed. Leave empty to let QA run the tests.</p>
        <div className="ap-label">Path rules</div>
        <p className="ap-muted ap-small">A change to a file matching these patterns, one per line, always asks before merging.</p>
        <div className="ap-rules">{(["security", "data", "ai"] as const).map(field => <label key={field} className="ap-field">
          <span>{RULE_LABEL[field]}</span>
          <textarea style={{ ...inputStyle, minHeight: 64, resize: "vertical" }} value={(draft.rules[field] ?? []).join("\n")}
            placeholder={RULE_HINT[field]}
            onChange={e => setDraft({ ...draft, rules: { ...draft.rules, [field]: e.target.value.split("\n") } })} />
        </label>)}</div>
      </details>
    </>}

    {error && <p className="ap-error-text" role="alert">{error}</p>}
    <div className="ap-foot">
      {editing && <span className="ap-foot-left"><Button variant="danger" size="sm" onClick={() => void remove()} disabled={busy}>Remove</Button></span>}
      {step === "triggers" && (!anyOn || !draft.repo) && <span className="ap-hint">{!draft.repo ? "Choose a repository" : "Turn on at least one trigger"}</span>}
      {step === "confirm"
        ? <Button variant="ghost" onClick={() => setStep("triggers")}>Back</Button>
        : <Button variant="ghost" onClick={onClose}>Cancel</Button>}
      {step === "triggers" && <Button onClick={() => void next()} disabled={!anyOn || !draft.repo || busy}>Next</Button>}
      {step === "confirm" && <Button onClick={() => void save()} disabled={busy}>{editing ? "Save" : "Turn on"}</Button>}
    </div>
  </Modal>;
}

// A workspace's Flows tab: the flows on autopilot in this workspace (and its
// subfolders) and their runs, one-off runs included. Managed on the Autopilot page.
export function WorkspaceFlows({ workspace }: { workspace: string }) {
  const [data, setData] = useState<AutopilotOverview | null>(null);
  const [error, setError] = useState("");
  const [openRun, setOpenRun] = useState<OpenRun | null>(null);
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
  if (openRun) return <div className="ap-page ap-embedded"><div className="ap-col">
    <RunDetail id={openRun.id} tab={openRun.tab} back="Flows" onBack={() => setOpenRun(null)} />
  </div></div>;
  const flowsById = new Map(data.flows.map(f => [f.id, f]));
  return <div className="ap-page ap-embedded"><div className="ap-col">
    {error && <p className="ap-error" role="alert">{error}</p>}
    <Waiting runs={data.runs} onAnswered={async () => setData(await autopilotApi.overview(workspace))} onOpen={setOpenRun} />
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
        : <RunTable runs={data.runs} onOpen={id => setOpenRun({ id })} />}
    </section>
  </div></div>;
}
