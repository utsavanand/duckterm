import { routedFetch as fetch, sessionFetch } from "./hostTransport";
import { useEffect, useRef, useState } from "react";
import { api, CheckpointRecord, CheckpointActivity, ContextEvidence } from "./api";
import { Duck, duckPhrase, poseFor } from "./Duck";
import { FileEditModal } from "./FileEditModal";
import { effectiveState, sessionStateLabel } from "./sessions";
import { SessionView } from "./types";
import { useToast } from "./ui";
import { CheckpointAgeValue, publishCheckpointClock } from "./CheckpointAge";
import { CheckpointFacts } from "./CheckpointStatus";
import { checkpointStatus, checkpointCanRetry } from "./checkpointState";
import { chooseCheckpoint } from "./checkpointSelection";
import { checkpointRequestError, useCheckpointAttempt } from "./checkpointRequests";
import { SessionPanelSection } from "./SessionPanelSection";
import { useEdition } from "./edition";

// Right pane: context about the selected agent's terminal. If the session is on
// a git branch, show a git view (branch, repo, and the working-tree diff). If
// not, show the folder it's running in. (Approvals render above this in App.)
export function ContextPanel({ session, active = true, onCheckpointTimeline, onMemory, onCheckpointMemory }: { session: SessionView; active?: boolean; onCheckpointTimeline?: () => void; onMemory?: (revision?: string) => void; onCheckpointMemory?: (id: string) => void }) {
  const toast = useToast();
  const { has } = useEdition();
  const checkpoints = has("checkpoints"), context = has("context"), git = has("git_changes");
  const [diff, setDiff] = useState<string>("");
  const [branches, setBranches] = useState<string[]>([]);
  const [acting, setActing] = useState<string | null>(null);
  const [editingFile, setEditingFile] = useState(false);
  const [lastCheckpoint, setLastCheckpoint] = useState<CheckpointRecord | null>(null);
  const [contextEvidence, setContextEvidence] = useState<ContextEvidence | null>(null);
  const [activity, setActivity] = useState<CheckpointActivity | null>(null);
  const [activityState, setActivityState] = useState<"loading" | "loaded" | "error">("loading");
  const checkpointGeneration = useRef(0);
  const [summaryOpen, setSummaryOpen] = useState(false);
  const [diffOpen, setDiffOpen] = useState(false);
  const [branchesOpen, setBranchesOpen] = useState(false);
  const localBranches = branches.filter((b) => !b.startsWith("origin/"));
  const onBranch = !!session.branch;
  const dir = session.worktreePath ?? session.cwd ?? null;
  const state = effectiveState(session, Date.now());
  const attempt = useCheckpointAttempt(session.key);
  const checkpointRunning = attempt?.state === "running" || (attempt?.state === "unknown" && attempt.recovering);
  const tokens = typeof session.contextTokens === "number" && Number.isFinite(session.contextTokens) && session.contextTokens >= 0 ? session.contextTokens.toLocaleString() : null;
  const compactAvailable = session.ptyOwned && ["claude-code", "codex"].includes(session.runtime ?? "");
  const harness = ({ "claude-code": "Claude Code", codex: "Codex", gemini: "Gemini CLI" } as Record<string, string>)[session.runtime ?? ""] ?? session.runtime ?? "Not reported";

  const eventCount = activity?.state === "known" && Number.isSafeInteger(activity.events_since_capture) && activity.events_since_capture! >= 0 ? activity.events_since_capture : null;
  const activityLabel = activityState === "loading" ? "Loading…" : activityState === "error" ? "Couldn’t load activity"
    : eventCount !== null ? eventCount.toLocaleString() : activity?.state === "never" ? "No successful checkpoint yet"
    : activity?.state === "unknown" ? "Capture boundary unavailable" : "Not provided by this server";

  const evidenceReady = activityState === "loaded" && contextEvidence?.state === "available";
  const capacity = evidenceReady && Number.isSafeInteger(contextEvidence?.context_window_tokens) && contextEvidence!.context_window_tokens! > 0 ? contextEvidence!.context_window_tokens : null;
  const compactedAt = evidenceReady && contextEvidence?.last_compaction_at && Number.isFinite(Date.parse(contextEvidence.last_compaction_at)) ? contextEvidence.last_compaction_at : null;
  const noEvidence = activityState === "loading" ? "Loading…" : activityState === "error" ? "Couldn’t load context" : !contextEvidence ? "Not provided by this server" : "Not reported";

  function checkpoint() {
    void chooseCheckpoint(session.key, session.label).catch(e => toast(checkpointRequestError(e), "err"));
  }

  async function compact() {
    setActing("compact");
    try {
      await api.sendInput(session.key, "/compact\n");
      toast("Sent /compact to the agent");
    } catch (e) {
      toast(`Compact failed: ${(e as Error).message}`, "err");
    } finally {
      setActing(null);
    }
  }

  useEffect(() => {
    setLastCheckpoint(null); setContextEvidence(null); setActivity(null); setActivityState("loading"); setActing(null); setSummaryOpen(false); checkpointGeneration.current++;
  }, [session.key]);
  useEffect(() => {
    if (!active || !(checkpoints || context)) return;
    let live = true, pending = false;
    const load = () => {
      if (!live || pending || document.visibilityState === "hidden") return;
      pending = true;
      const generation = checkpointGeneration.current;
      void api.checkpoints(session.key).then(d => {
        if (live && generation === checkpointGeneration.current) { setLastCheckpoint(d.checkpoints[0] ?? null); setContextEvidence(d.context_evidence ?? null); setActivity(d.checkpoint_activity ?? null); setActivityState("loaded"); publishCheckpointClock(session.key, d.checkpoint_clock); }
      }).catch(() => { if (live && generation === checkpointGeneration.current) { setActivity(null); setContextEvidence(null); setActivityState("error"); publishCheckpointClock(session.key, null); } }).finally(() => { pending = false; });
    };
    const updated = (event: Event) => { if ((event as CustomEvent<string>).detail === session.key) load(); };
    load();
    const timer = setInterval(load, 10_000);
    document.addEventListener("visibilitychange", load);
    window.addEventListener("duckterm-checkpoint", updated);
    return () => { live = false; clearInterval(timer); document.removeEventListener("visibilitychange", load); window.removeEventListener("duckterm-checkpoint", updated); };
  }, [session.key, active, checkpoints, context]);

  useEffect(() => {
    setDiffOpen(false); // a new session starts with the diff collapsed
    setBranchesOpen(false);
    setEditingFile(false);
  }, [session.key]);

  useEffect(() => {
    // The diff is heavy and rarely needed — fetch it only when opened.
    if (!git || !onBranch || !diffOpen) return;
    setDiff("");
    fetch(`/sessions/${session.key}/diff`)
      .then((r) => r.json())
      .then((d: { diff?: string; error?: string }) =>
        setDiff(d.error ? `git diff failed: ${d.error}` : (d.diff ?? "")),
      )
      .catch(() => setDiff(""));
  }, [session.key, git, onBranch, diffOpen]);

  useEffect(() => {
    if (!git || !onBranch || !dir) return;
    setBranches([]);
    sessionFetch(session.key, `/branches?path=${encodeURIComponent(dir)}`)
      .then((r) => r.json())
      .then((d: { branches?: string[] }) => setBranches(d.branches ?? []))
      .catch(() => setBranches([]));
  }, [dir, git, onBranch, session.key]);

  return (
    <div className="rd-context">
      <header className="rd-session-overview">
        <div className="rd-session-overview-title"><Duck key={session.key} pose={poseFor(state, !!session.attentionSince)} size={28} celebrating={session.celebration} />
          <h2>{session.label}</h2><span className="rd-session-overview-state">{sessionStateLabel(state)}</span></div>
        <div className="rd-section-purpose">{session.group || "Ungrouped"}{session.branch && <> / {session.branch}</>}</div>
        {has("summaries") && session.progress?.summary ? <button className={`rd-session-work${summaryOpen ? " open" : ""}`} aria-expanded={summaryOpen} onClick={() => setSummaryOpen(o => !o)} title={summaryOpen ? "Collapse current work" : "Expand current work"}><span className="rd-session-work-text">{session.progress.summary}</span></button>
          : <p className="rd-session-work open">{duckPhrase(session, state)}</p>}
        <div className="rd-session-overview-model">{harness} <span>·</span> <strong>{session.model || "Model not reported yet"}</strong></div>
      </header>
      {checkpoints && <SessionPanelSection key={`memory-${session.key}`} title="Saved memory" initiallyOpen status={<CheckpointAgeValue sessionKey={session.key} />}>
        <p className="rd-section-purpose">Knowledge kept for future sessions.</p>
        <div className="rd-session-metrics"><div><span>Last successful save</span><strong><CheckpointAgeValue sessionKey={session.key} /></strong></div>
          <div><span>Events since saved capture</span><strong className={eventCount === null ? "rd-value-unavailable" : undefined}>{activityLabel}</strong></div></div>
        {lastCheckpoint && <details className="rd-section-disclosure" aria-label="Latest checkpoint">
          <summary>Latest attempt · {checkpointStatus(lastCheckpoint).label}</summary>
          <CheckpointFacts checkpoint={lastCheckpoint} compact onMemory={onMemory} />
          <div className="rd-section-actions">{checkpointCanRetry(lastCheckpoint) && <button className="rd-timeline-link" disabled={!!checkpointRunning || !!session.hostOffline} onClick={checkpoint}>Retry checkpoint</button>}
          {onCheckpointTimeline && <button className="rd-timeline-link" onClick={onCheckpointTimeline}>Latest checkpoint ↗</button>}</div>
        </details>}
        <div className="rd-section-actions">{lastCheckpoint && onCheckpointMemory && <button className="rd-timeline-link" onClick={() => onCheckpointMemory(lastCheckpoint.id)}>Read checkpoint result ↗</button>}{onMemory && <button className="rd-timeline-link" onClick={() => onMemory()}>Read saved memory ↗</button>}
          <button className="rd-btn rd-btn-sm rd-btn-ghost" disabled={!!checkpointRunning || !!session.hostOffline} onClick={checkpoint}>Checkpoint</button></div>
        <details className="rd-section-disclosure rd-section-help"><summary>About saved memory</summary><p>Checkpoint saves a durable summary. A failed result does not certify readiness; saved memory may have advanced before a final source check failed.</p><p>Counts use the last successful checkpoint’s captured history boundary, including events recorded while that save was running. Events are recorded activity, not conversation turns or unsummarized records; older summary gaps may still exist.</p>{activity?.event_filter && <p>{activity.event_filter === "work-v1" ? "Uses the saved work-event filter; attendance and status-only notifications are excluded." : "Uses the saved all-events filter, including status observations."}</p>}</details>
      </SessionPanelSection>}
      {context && <SessionPanelSection key={`context-${session.key}`} title="Active context" initiallyOpen status={tokens ? `${tokens} tokens` : "Unknown"}>
        <p className="rd-section-purpose">Conversation currently used by the agent.</p>
        <div className="rd-session-context-total"><strong>{tokens ?? "Unknown"}</strong>{tokens && <span>tokens reported</span>}</div>
        <dl className="rd-session-facts"><div><dt>Context window</dt><dd>{capacity !== null ? `${capacity.toLocaleString()} tokens` : noEvidence}</dd></div><div><dt>Last compaction</dt><dd>{compactedAt ? <time dateTime={compactedAt}>{new Date(compactedAt).toLocaleString()}</time> : noEvidence}</dd></div></dl>
        <div className="rd-section-actions rd-section-separated"><span>Shorten the active conversation.<br />Saved memory stays separate.</span><button className="rd-btn rd-btn-sm rd-btn-ghost" disabled={!compactAvailable || acting !== null || !!session.hostOffline} title={compactAvailable ? "Types /compact into the agent’s terminal" : "Requires a running Claude Code or Codex terminal"} onClick={compact}>{acting === "compact" ? "Sending…" : "Compact"}</button></div>
        <details className="rd-section-disclosure rd-section-help"><summary>About context &amp; compaction</summary><p>Window size and completed compaction come only from this conversation’s native transcript. A high token count or a drop in usage does not prove compaction. Missing values mean there is no verified report available.</p>{contextEvidence?.history_scope === "recent" && <p>Only recent history was inspected. A missing compaction marker does not mean the conversation has never been compacted.</p>}<p>Compact asks the harness to condense its active conversation. It does not create a DuckTerm checkpoint.</p></details>
      </SessionPanelSection>}
      <SessionPanelSection key={`workspace-${session.key}`} title="Workspace" status={session.branch ? `${session.repoName || "Repository"} / ${session.branch}` : "Folder"}>
      {onBranch ? (
        <div className="rd-context-git">
          <div className="rd-context-repository">
            {session.repoName && (
              <span className="rd-context-repo" title={dir ?? undefined}>
                {session.repoName}
              </span>
            )}
            <span className="rd-context-current-branch" title={`Current branch: ${session.branch}`}>
              <span aria-hidden="true">⑂</span> {session.branch}
            </span>
          </div>
          {git && <div className="rd-context-git-actions">
            {localBranches.length > 0 && (
              <button
                className="rd-context-toggle"
                aria-expanded={branchesOpen}
                onClick={() => setBranchesOpen((o) => !o)}
              >
                Branches ({localBranches.length}) {branchesOpen ? "▾" : "▸"}
              </button>
            )}
            <button
              className="rd-context-toggle"
              aria-expanded={diffOpen}
              onClick={() => setDiffOpen((o) => !o)}
            >
              Working-tree diff {diffOpen ? "▾" : "▸"}
            </button>
          </div>}
          {branchesOpen && (
            <ul className="rd-branch-list">
              {localBranches.map((b) => (
                <li key={b} className={b === session.branch ? "rd-branch current" : "rd-branch"}>
                  <span className="rd-branch-mark">{b === session.branch ? "●" : "○"}</span>
                  <span className="mono" title={b}>{b}</span>
                </li>
              ))}
            </ul>
          )}
          {diffOpen && (
            <pre className="rd-context-diff">
              {diff || "No uncommitted changes."}
            </pre>
          )}
        </div>
      ) : (
        <div className="rd-context-folder">
          <div className="rd-context-section-title">Folder</div>
          <code className="rd-context-path">{session.cwd ?? "—"}</code>
        </div>
      )}
      {dir && has("file_editing") && <div className="rd-section-actions"><button className="rd-btn rd-btn-sm rd-btn-ghost" onClick={() => setEditingFile(true)}>Edit file</button></div>}
      </SessionPanelSection>
      {editingFile && dir && has("file_editing") && (
        <FileEditModal sessionKey={session.key} dir={dir} onClose={() => setEditingFile(false)} />
      )}
    </div>
  );
}
