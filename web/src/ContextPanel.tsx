import { useEffect, useState } from "react";
import { api } from "./api";
import { FileEditModal } from "./FileEditModal";
import { contextLevel, contextWindowFor, fmtTokens } from "./sessions";
import { SessionView } from "./types";
import { useToast } from "./ui";

function checkpointFresh(ts: number | null): boolean {
  return ts !== null && Date.now() - ts < 30 * 60_000;
}

function agoShort(ts: number): string {
  const mins = Math.max(0, Math.round((Date.now() - ts) / 60_000));
  return mins < 1 ? "just now" : `${mins}m ago`;
}

function age(startedAt: number): string {
  const mins = Math.max(0, Math.round((Date.now() - startedAt) / 60_000));
  if (mins < 60) return `${mins}m`;
  const h = Math.floor(mins / 60);
  return h < 24 ? `${h}h ${mins % 60}m` : `${Math.floor(h / 24)}d ${h % 24}h`;
}

// Right pane: context about the selected agent's terminal. If the session is on
// a git branch, show a git view (branch, repo, and the working-tree diff). If
// not, show the folder it's running in. (Approvals render above this in App.)
export function ContextPanel({ session }: { session: SessionView }) {
  const toast = useToast();
  const [diff, setDiff] = useState<string>("");
  const [branches, setBranches] = useState<string[]>([]);
  const [acting, setActing] = useState<string | null>(null);
  const [editingFile, setEditingFile] = useState(false);
  // Newest checkpoint timestamp — the context warning acknowledges a fresh
  // one instead of nagging as if nothing happened.
  const [lastCheckpoint, setLastCheckpoint] = useState<number | null>(null);
  const [summaryOpen, setSummaryOpen] = useState(false);
  const onBranch = !!session.branch;
  const dir = session.worktreePath ?? session.cwd ?? null;
  const ctxLevel = contextLevel(session.contextTokens, session.model);

  async function checkpoint() {
    setActing("checkpoint");
    try {
      await api.checkpoint(session.key, "context-full");
      setLastCheckpoint(Date.now());
      toast("Checkpoint recorded — see the History tab");
    } catch (e) {
      toast(`Checkpoint failed: ${(e as Error).message}`, "err");
    } finally {
      setActing(null);
    }
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
    setLastCheckpoint(null);
    api
      .checkpoints(session.key)
      .then((d) => setLastCheckpoint(d.checkpoints[0]?.created_at ?? null))
      .catch(() => undefined);
  }, [session.key]);

  useEffect(() => {
    if (!onBranch) return;
    setDiff("");
    fetch(`/sessions/${session.key}/diff`)
      .then((r) => r.json())
      .then((d: { diff?: string; error?: string }) =>
        setDiff(d.error ? `git diff failed: ${d.error}` : (d.diff ?? "")),
      )
      .catch(() => setDiff(""));
  }, [session.key, onBranch]);

  useEffect(() => {
    if (!onBranch || !dir) return;
    setBranches([]);
    fetch(`/branches?path=${encodeURIComponent(dir)}`)
      .then((r) => r.json())
      .then((d: { branches?: string[] }) => setBranches(d.branches ?? []))
      .catch(() => setBranches([]));
  }, [dir, onBranch]);

  return (
    <div className="rd-context">
      {/* The latest running summary (3-4 lines); the full digest — delivered,
          learnings, next actions — lives in the middle pane's History tab. */}
      {session.progress?.summary && (
        <p
          className={`rd-context-summary${summaryOpen ? " open" : ""}`}
          title={
            summaryOpen
              ? "Click to collapse"
              : "Click to expand — full digest in the History tab"
          }
          onClick={() => setSummaryOpen((o) => !o)}
        >
          <span className="rd-context-summary-text">
            {session.progress.summary}
          </span>
        </p>
      )}
      <div className="rd-context-meta">
        <div className="rd-context-row">
          <span className="k">state</span>
          <span className="v">{session.state}</span>
        </div>
        <div className="rd-context-row">
          <span className="k">harness</span>
          <span className="v">{session.runtime ?? "—"}</span>
        </div>
        {session.metaHarnesses && session.metaHarnesses.length > 0 && (
          <div className="rd-context-row">
            <span className="k">meta harness</span>
            <span className="v">{session.metaHarnesses.join(", ")}</span>
          </div>
        )}
        {session.model && (
          <div className="rd-context-row">
            <span className="k">model</span>
            <span className="v">{session.model}</span>
          </div>
        )}
        <div className="rd-context-row">
          <span className="k">started</span>
          <span className="v">
            {new Date(session.startedAt).toLocaleTimeString([], {
              hour: "2-digit",
              minute: "2-digit",
            })}{" "}
            · up {age(session.startedAt)}
          </span>
        </div>
        {session.contextTokens != null && (
          <div className="rd-context-row">
            <span className="k">context</span>
            <span className={`v${ctxLevel ? ` ctx-${ctxLevel}` : ""}`}>
              {fmtTokens(session.contextTokens)} used ·{" "}
              {fmtTokens(
                Math.max(
                  0,
                  contextWindowFor(session.model) - session.contextTokens,
                ),
              )}{" "}
              left
            </span>
          </div>
        )}
        {session.intention && (
          <div className="rd-context-row">
            <span className="k">intent</span>
            <span className="v">{session.intention}</span>
          </div>
        )}
      </div>

      {ctxLevel && (
        <div className={`rd-ctx-warning ${ctxLevel}`}>
          <div className="rd-ctx-warning-text">
            {checkpointFresh(lastCheckpoint)
              ? `Checkpointed ${agoShort(lastCheckpoint!)} — resumable. Only /compact frees context; run it when convenient.`
              : ctxLevel === "high"
                ? "Context is nearly full — checkpoint the session, or compact it before quality degrades."
                : "This session has been going a while and its context is filling up — a checkpoint now makes it resumable later."}
          </div>
          <div className="rd-ctx-warning-actions">
            {!checkpointFresh(lastCheckpoint) && (
              <button
                className="rd-btn rd-btn-sm rd-btn-ghost"
                disabled={acting !== null}
                onClick={checkpoint}
              >
                {acting === "checkpoint" ? "Capturing…" : "Checkpoint"}
              </button>
            )}
            {session.ptyOwned && (
              <button
                className="rd-btn rd-btn-sm rd-btn-ghost"
                disabled={acting !== null}
                title="Types /compact into the agent's terminal"
                onClick={compact}
              >
                {acting === "compact" ? "Sending…" : "Compact"}
              </button>
            )}
          </div>
        </div>
      )}

      {onBranch ? (
        <div className="rd-context-git">
          <div className="rd-context-section-title">Git</div>
          <div className="rd-context-row">
            <span className="k">branch</span>
            <span className="v mono">{session.branch}</span>
          </div>
          {session.repoName && (
            <div className="rd-context-row">
              <span className="k">repo</span>
              <span className="v mono">{session.repoName}</span>
            </div>
          )}
          {branches.length > 0 && (
            <>
              <div className="rd-context-section-title">Branches</div>
              <ul className="rd-branch-list">
                {branches
                  .filter((b) => !b.startsWith("origin"))
                  .map((b) => (
                    <li
                      key={b}
                      className={
                        b === session.branch ? "rd-branch current" : "rd-branch"
                      }
                    >
                      <span className="rd-branch-mark">
                        {b === session.branch ? "●" : "○"}
                      </span>
                      <span className="mono">{b}</span>
                    </li>
                  ))}
              </ul>
            </>
          )}
          <div className="rd-context-section-title">Working-tree diff</div>
          <pre className="rd-context-diff">
            {diff || "No uncommitted changes."}
          </pre>
        </div>
      ) : (
        <div className="rd-context-folder">
          <div className="rd-context-section-title">Folder</div>
          <code className="rd-context-path">{session.cwd ?? "—"}</code>
        </div>
      )}
      {dir && (
        <button
          className="rd-btn rd-btn-ghost rd-btn-sm"
          title="Edit a file in this session's folder (e.g. fill in a .env the agent asked for) — content goes straight to disk, never through the agent"
          onClick={() => setEditingFile(true)}
        >
          Edit file…
        </button>
      )}
      {editingFile && dir && (
        <FileEditModal dir={dir} onClose={() => setEditingFile(false)} />
      )}
    </div>
  );
}
