import { useCallback, useEffect, useRef, useState } from "react";
import { authHeaders } from "./api";
import { SessionView } from "./types";
import { useToast } from "./ui";

interface Approval {
  id: string;
  session_key: string;
  tool_name: string;
  detail: string;
  created_at: number;
  reachable: boolean;
}

// Surfaces what needs you: pending permission requests (Approve/Deny without
// switching terminals) AND sessions waiting on an answer — but only OTHER
// sessions: the selected one's question is already on screen in its terminal,
// and its waiting state shows in the left panel row. These rows are jump links.
export function Approvals({
  labels,
  pollKey,
  onOpen,
  knownKeys,
  waiting,
  selectedKey,
}: {
  labels: Record<string, string>;
  pollKey: number;
  onOpen: (key: string) => void;
  knownKeys: Set<string>;
  waiting: SessionView[];
  selectedKey: string | null;
}) {
  const toast = useToast();
  const [approvals, setApprovals] = useState<Approval[]>([]);
  const [expanded, setExpanded] = useState(false);
  // A NEW actionable approval auto-expands once; collapsing again sticks.
  const approvalIds = approvals.map((a) => a.id).join(",");
  const prevIds = useRef("");
  useEffect(() => {
    if (approvalIds && approvalIds !== prevIds.current) setExpanded(true);
    prevIds.current = approvalIds;
  }, [approvalIds]);

  const refresh = useCallback(() => {
    fetch("/approvals")
      .then((r) => r.json())
      .then((d: { approvals: Approval[] }) => setApprovals(d.approvals))
      .catch(() => undefined);
  }, []);

  useEffect(() => {
    refresh();
    const t = setInterval(refresh, 2000);
    return () => clearInterval(t);
  }, [refresh, pollKey]);

  async function decide(id: string, decision: "approve" | "deny") {
    try {
      const res = await fetch(`/approvals/${id}/decide`, {
        method: "POST",
        headers: authHeaders({ "Content-Type": "application/json" }),
        body: JSON.stringify({ decision }),
      });
      const data = await res.json();
      if (res.ok && data.decided) {
        toast(decision === "approve" ? "Approved" : "Denied");
      } else {
        toast(
          "Couldn't reach the agent — only Duckterm-launched sessions can be answered",
          "err",
        );
      }
      refresh();
    } catch {
      toast("Decision failed", "err");
    }
  }

  // Sessions that are waiting but aren't already covered by a pending approval
  // (the agent asked a question and paused, vs. a tool-permission prompt).
  const approvalKeys = new Set(approvals.map((a) => a.session_key));
  const asking = waiting.filter(
    (s) => !approvalKeys.has(s.key) && s.key !== selectedKey,
  );
  const total = approvals.length + asking.length;

  if (total === 0)
    return <p className="rd-panel-empty">Nothing needs you right now.</p>;

  // One line by default, pinned at the top; expanding reveals a scroll area
  // capped at ~35% of the panel so it can never crowd out the context below.
  return (
    <div className={`rd-approvals${expanded ? " expanded" : ""}`}>
      <button className="rd-approvals-line" onClick={() => setExpanded((e) => !e)}>
        <span className="dot" />
        {approvals.length > 0 && (
          <b>
            {approvals.length} approval{approvals.length > 1 ? "s" : ""}
          </b>
        )}
        {approvals.length > 0 && asking.length > 0 && " · "}
        {asking.length > 0 && (
          <>
            {asking.length} waiting
          </>
        )}
        <span className="rd-approvals-caret">{expanded ? "▾" : "▸"}</span>
      </button>
      {expanded && (
        <div className="rd-approvals-body">
      {approvals.map((a) => (
        <div className="rd-approval" key={a.id}>
          {(() => {
            const openable = knownKeys.has(a.session_key);
            return (
              <div
                style={{ flex: 1, cursor: openable ? "pointer" : "default" }}
                onClick={openable ? () => onOpen(a.session_key) : undefined}
                title={openable ? "Open session details" : undefined}
              >
                <div className="who">
                  {labels[a.session_key] ?? a.session_key.slice(0, 8)} ·{" "}
                  {a.tool_name}
                  {a.created_at > 0 && (
                    <span className="when">
                      {new Date(a.created_at).toLocaleTimeString()}
                    </span>
                  )}
                </div>
                {a.detail && (
                  <div className="what">
                    <code>{a.detail}</code>
                  </div>
                )}
              </div>
            );
          })()}
          {a.reachable ? (
            <>
              <button
                className="rd-btn rd-btn-sm rd-btn-ghost"
                onClick={() => decide(a.id, "deny")}
              >
                Deny
              </button>
              <button
                className="rd-btn rd-btn-sm rd-btn-primary"
                onClick={() => decide(a.id, "approve")}
              >
                Approve
              </button>
            </>
          ) : (
            // Watched session: Duckterm doesn't own its terminal, so it can't
            // answer. Just say "watched"; the tooltip explains where to answer.
            <span
              className="rd-origin watched"
              title="Watched session — answer this in its own terminal; Duckterm can only answer sessions it launched."
            >
              watched
            </span>
          )}
        </div>
      ))}
      {asking.length > 0 && (
        <div className="rd-waiting-list">
          {/* Compact jump rows: click to open that session's terminal. */}
          {asking.map((s) => (
            <button
              className="rd-waiting-row"
              key={s.key}
              disabled={!knownKeys.has(s.key)}
              onClick={() => onOpen(s.key)}
              title="Jump to this session"
            >
              <span className="dot" style={{ background: "var(--wait)" }} />
              {labels[s.key] ?? s.label}
            </button>
          ))}
        </div>
      )}
        </div>
      )}
    </div>
  );
}
