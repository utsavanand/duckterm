import { routedFetch as fetch, sessionFetch } from "./hostTransport";
import { useEffect, useRef, useState } from "react";
import { api, CheckpointRecord, ContextEvidence } from "./api";
import { Duck, duckPhrase, poseFor } from "./Duck";
import { FileEditModal } from "./FileEditModal";
import { effectiveState, sessionStateLabel } from "./sessions";
import { SessionView } from "./types";
import { useToast } from "./ui";
import { publishCheckpointClock } from "./CheckpointAge";
import { SavedMemoryTable } from "./SavedMemoryTable";
import { CheckpointProgress } from "./CheckpointProgress";
import { checkpointStatus, checkpointCanRetry, checkpointAttemptFailed } from "./checkpointState";
import { chooseCheckpoint } from "./checkpointSelection";
import { checkpointRequestError, useCheckpointAttempt } from "./checkpointRequests";
import { SessionPanelSection } from "./SessionPanelSection";
import { useEdition } from "./edition";

// Right pane: context about the selected agent's terminal. If the session is on
// a git branch, show a git view (branch, repo, and the working-tree diff). If
// not, show the folder it's running in. (Approvals render above this in App.)
export function ContextPanel({ session, active = true, onMemory, onCheckpointMemory }: { session: SessionView; active?: boolean; onCheckpointTimeline?: () => void; onMemory?: (revision?: string) => void; onCheckpointMemory?: (id: string) => void }) {
  const toast = useToast();
  const { has } = useEdition();
  const checkpoints = has("checkpoints"), context = has("context"), git = has("git_changes");
  const [diff, setDiff] = useState<string>("");
  const [branches, setBranches] = useState<string[]>([]);
  const [acting, setActing] = useState<string | null>(null);
  const [editingFile, setEditingFile] = useState(false);
  const [lastCheckpoint, setLastCheckpoint] = useState<CheckpointRecord | null>(null);
  const [contextEvidence, setContextEvidence] = useState<ContextEvidence | null>(null);
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
    setLastCheckpoint(null); setContextEvidence(null); setActivityState("loading"); setActing(null); setSummaryOpen(false); checkpointGeneration.current++;
  }, [session.key]);
  useEffect(() => {
    if (!active || !(checkpoints || context)) return;
    let live = true, pending = false;
    const load = () => {
      if (!live || pending || document.visibilityState === "hidden") return;
      pending = true;
      const generation = checkpointGeneration.current;
      void api.checkpoints(session.key).then(d => {
        if (live && generation === checkpointGeneration.current) { setLastCheckpoint(d.checkpoints[0] ?? null); setContextEvidence(d.context_evidence ?? null); setActivityState("loaded"); publishCheckpointClock(session.key, d.checkpoint_clock); }
      }).catch(() => { if (live && generation === checkpointGeneration.current) { setContextEvidence(null); setActivityState("error"); publishCheckpointClock(session.key, null); } }).finally(() => { pending = false; });
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
      {checkpoints && <SessionPanelSection key={`memory-${session.key}`} title="Saved memory" initiallyOpen forceOpen={!!checkpointRunning}>
        <SavedMemoryTable key={session.key} sessionKey={session.key} active={active} onMemory={onMemory} />
        {attempt && attempt.state !== "complete" && <CheckpointProgress sessionKey={session.key} compact />}
        {!checkpointRunning && lastCheckpoint && (checkpointAttemptFailed(lastCheckpoint) || checkpointCanRetry(lastCheckpoint)) && <p className="rd-session-save-status">
          {onCheckpointMemory ? <button className="rd-timeline-link" onClick={() => onCheckpointMemory(lastCheckpoint.id)}>Latest checkpoint · {checkpointStatus(lastCheckpoint).label} ↗</button>
            : checkpointStatus(lastCheckpoint).label}
        </p>}
        <div className="rd-section-actions rd-memory-actions"><button className="rd-btn rd-btn-sm rd-btn-ghost" disabled={!!checkpointRunning || !!session.hostOffline} onClick={checkpoint}>{lastCheckpoint && checkpointCanRetry(lastCheckpoint) ? "Retry checkpoint" : "Checkpoint"}</button></div>
      </SessionPanelSection>}
      {context && <SessionPanelSection key={`context-${session.key}`} title="Active context" initiallyOpen status={tokens !== null ? `${tokens} used` : "Unknown"}>
        <div className="rd-session-token-line" aria-label="Context tokens">
          <span>{tokens ?? "—"} used</span><span aria-hidden="true"> · </span>
          <span title={capacity !== null && tokens !== null ? "Remaining reported context capacity" : noEvidence}>{capacity !== null && tokens !== null ? Math.max(0, capacity - session.contextTokens!).toLocaleString() : "—"} left</span>
        </div>
        <div className="rd-section-actions"><span>{compactedAt && <>Compacted <time dateTime={compactedAt}>{new Date(compactedAt).toLocaleString([], { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" })}</time></>}</span>
          <button className="rd-btn rd-btn-sm rd-btn-ghost" disabled={!compactAvailable || acting !== null || !!session.hostOffline} title={compactAvailable ? "Types /compact into the agent’s terminal" : "Requires a running Claude Code or Codex terminal"} onClick={compact}>{acting === "compact" ? "Sending…" : "Compact"}</button></div>
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
