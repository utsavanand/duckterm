import { useCallback, useEffect, useState } from "react";
import { api, KitRun, KitRunSummary } from "./api";
import { Button, useToast } from "./ui";
import "./skillKits.css";

// The Flows tab of a session: every flow run in the session's repository,
// the ones this session started first, each with its steps and, when it
// waits at a gate, the gate's question with Approve and Reject under the
// step. Read from the run files through /kits/runs and /kits/run-detail,
// refreshed every few seconds while anything runs.

const POLL_MS = 4000;

function tone(state: string): string {
  if (state === "succeeded") return "ok";
  if (state === "failed") return "bad";
  if (state === "waiting_gate") return "warn";
  return "";
}

function label(state: string): string {
  return state === "waiting_gate" ? "waiting for you" : state;
}

function ago(ts: number | null | undefined): string {
  if (!ts) return "";
  const s = Math.max(0, Math.floor(Date.now() / 1000 - ts));
  if (s < 60) return `${s} s ago`;
  if (s < 3600) return `${Math.floor(s / 60)} min ago`;
  if (s < 86400) return `${Math.floor(s / 3600)} h ago`;
  return `${Math.floor(s / 86400)} d ago`;
}

export function FlowsView({ project, sessionKey, context = "" }: { project: string | null; sessionKey: string; context?: string }) {
  const [runs, setRuns] = useState<KitRunSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    if (!project) return;
    try {
      const out = await api.kitsRuns(project, context);
      setRuns(out.runs);
      setError(null);
    } catch (e) {
      setError((e as Error).message);
    }
  }, [project, context]);

  useEffect(() => {
    refresh();
    const handle = setInterval(refresh, POLL_MS);
    return () => clearInterval(handle);
  }, [refresh]);

  if (!project) return <p className="rd-panel-empty">Select a session to see its flows.</p>;
  if (error) return <div className="rd-kits-error">{error}</div>;
  if (runs === null) return <p className="rd-panel-empty">Reading runs…</p>;
  const mine = runs.filter((r) => r.session === sessionKey);
  const others = runs.filter((r) => r.session !== sessionKey);
  const ordered = [...mine, ...others].sort((a, b) => (b.started_at ?? 0) - (a.started_at ?? 0));
  if (ordered.length === 0) return <p className="rd-panel-empty">No flow has run in this repository yet. A flow runs on its own when it has a trigger, otherwise through its shortcut in a session.</p>;
  return (
    <div className="rd-run">
      {ordered.map((r, i) => <RunBox key={r.run_id} summary={r} project={project} context={context} mine={r.session === sessionKey} open={i === 0 || r.state === "waiting_gate" || r.state === "running"} onChange={refresh} />)}
    </div>
  );
}

function RunBox({ summary, project, context, mine, open, onChange }: { summary: KitRunSummary; project: string; context: string; mine: boolean; open: boolean; onChange: () => void }) {
  const toast = useToast();
  const [detail, setDetail] = useState<{ run: KitRun; steps: { id: string; kind: string; skill?: string | null; question?: string | null; routes?: Record<string, string> | null }[] } | null>(null);
  const [isOpen, setOpen] = useState(open);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!isOpen) return;
    let cancelled = false;
    api.kitsRunDetail(project, summary.run_id, context).then((d) => { if (!cancelled) setDetail(d); }).catch(() => undefined);
    return () => { cancelled = true; };
  }, [isOpen, project, summary.run_id, summary.updated_at, context]);

  async function answer(step: string, choice: string) {
    setBusy(true);
    try {
      await api.kitsGate(project, summary.run_id, step, choice, context);
      toast(`${choice}: ${summary.flow.split("/")[1]} continues`, "ok");
      onChange();
    } catch (e) {
      toast(`Gate refused: ${(e as Error).message}`, "err");
    } finally {
      setBusy(false);
    }
  }

  const [kit, flow] = summary.flow.split("/");
  const inputs = Object.entries(summary.inputs ?? {}).map(([k, v]) => `${k}="${v}"`).join(" ");
  // a run file this machine did not write (a clone's, a commit's) is shown but
  // offers no gate: the server refuses to resume it, so there is nothing to answer
  const foreign = summary.recorded === false;
  const gate = foreign ? null : (detail?.run.gates.find((g) => g.response === null) ?? null);
  const attemptsOf = (id: string) => (detail?.run.attempts ?? []).filter((a) => a.step === id);
  const stepState = (id: string): { text: string; tone: string } => {
    const atts = attemptsOf(id);
    const last = atts[atts.length - 1];
    if (gate && gate.step === id) return { text: "waiting for you", tone: "warn" };
    if (!last) return detail?.run.current?.step === id ? { text: "running", tone: "" } : { text: "", tone: "mute" };
    if (last.status === "completed") return { text: last.choice ? `done · ${last.choice}` : "done", tone: "ok" };
    if (last.status === "started") return { text: "running", tone: "" };
    if (last.status === "invalid") return { text: `retrying · ${last.reason}`, tone: "warn" };
    return { text: last.status + (last.reason ? ` · ${last.reason}` : ""), tone: "bad" };
  };

  return (
    <details className="rd-runbox" open={isOpen} onToggle={(e) => setOpen((e.currentTarget as HTMLDetailsElement).open)}>
      <summary>
        <div>
          <strong>{flow}</strong> <span className="rd-kits-mono" style={{ color: "var(--text-soft)" }}>{kit} · {summary.run_id}</span>
          {foreign && <span className="rd-kits-sub"> · not started on this machine</span>}
          <div className="rd-kits-sub">{ago(summary.started_at)} · <span className="rd-kits-mono">/{flow}{inputs ? ` ${inputs}` : ""}</span>{summary.current ? ` · step ${summary.current.step}` : ""}{!mine && summary.session ? " · another session" : ""}</div>
        </div>
        <span className={`rd-kit-pill ${tone(summary.state)}`}>{label(summary.state)}</span>
      </summary>
      {detail && (
        <div className="rd-steps">
          {detail.steps.map((st, i) => {
            const s = stepState(st.id);
            const atts = attemptsOf(st.id);
            const last = atts[atts.length - 1];
            const outputs = last?.outputs ? Object.entries(last.outputs).filter(([, v]) => v && typeof v === "object" && "path" in (v as object)) : [];
            return (
              <div key={st.id}>
                <div className={`rd-step${s.tone === "ok" ? " done" : ""}${gate?.step === st.id || s.text === "running" ? " now" : ""}`}>
                  <span className="n">{i + 1}</span>
                  <div>
                    <div className="t">{st.id} <span className="rd-kits-mono" style={{ color: "var(--text-soft)" }}>{st.skill ?? st.kind}</span></div>
                    {(last?.reason || outputs.length > 0 || st.question) && (
                      <div className="rd-kits-note">
                        {st.kind === "gate" && st.question}
                        {last?.reason && st.kind !== "gate" ? last.reason : ""}
                        {outputs.map(([name, v]) => <span key={name}> · <span className="rd-kits-mono">{(v as { path: string }).path.split("/").pop()}</span></span>)}
                      </div>
                    )}
                  </div>
                  <span className={`rd-kit-pill ${s.tone}`}>{s.text}</span>
                </div>
                {gate && gate.step === st.id && (
                  <div className="rd-gate">
                    {gate.show !== null && typeof gate.show === "object" && "path" in (gate.show as object) ? (
                      <div className="rd-kits-note" style={{ margin: 0 }}>Shows <span className="rd-kits-mono">{(gate.show as { path: string }).path}</span></div>
                    ) : null}
                    <div className="rd-kits-actions">
                      {gate.choices.map((c) => <Button key={c} size="sm" variant={c === "approve" ? "primary" : "ghost"} disabled={busy} onClick={() => answer(gate.step, c)}>{c}</Button>)}
                      <span className="rd-kits-mono" style={{ alignSelf: "center", color: "var(--text-soft)" }}>visit {gate.visit}</span>
                    </div>
                  </div>
                )}
              </div>
            );
          })}
          {summary.failure && <div className="rd-kits-error">Failed at {summary.failure.step}: {summary.failure.reason}</div>}
        </div>
      )}
    </details>
  );
}
