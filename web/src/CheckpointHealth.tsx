import { useEffect, useState } from "react";
import { api, type AgentMemoryHealth, type CheckpointHealth as Health } from "./api";
import { Button } from "./ui";

const STATUS: Record<AgentMemoryHealth["status"], string> = {
  failing: "Failing", none: "No memory yet", behind: "Behind", current: "Current",
};
const REASONS: Record<string, string> = {
  source_unavailable: "couldn't read its history", source_changed: "work changed during the update",
  invalid_context: "the summary couldn't be verified", update_failed: "the update couldn't finish",
  summary_unverified: "the summary didn't pass validation", provider_timeout: "the model timed out",
};

function ago(ms: number | null, now: number) {
  if (!ms) return "Never";
  const minutes = Math.max(0, Math.round((now - ms) / 60000));
  if (minutes < 60) return `${minutes} min ago`;
  if (minutes < 48 * 60) return `${Math.round(minutes / 60)} h ago`;
  return `${Math.round(minutes / 1440)} days ago`;
}

function since(a: AgentMemoryHealth, now: number) {
  if (a.status === "failing" && a.last_attempt) {
    return `Last attempt ${ago(a.last_attempt.at, now)}: ${REASONS[a.last_attempt.reason ?? ""] ?? "it failed"}`;
  }
  if (!a.new_messages) return "Up to date";
  return `${a.new_messages.toLocaleString()} ${a.new_messages === 1 ? "message" : "messages"} ${a.saved_at ? "since" : "not summarized"}`;
}

export function CheckpointHealth({ onOpenAgent }: { onOpenAgent?: (key: string) => void }) {
  const [health, setHealth] = useState<Health | null>(null);
  const [error, setError] = useState("");
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    let canceled = false;
    setError("");
    void api.checkpointHealth().then(value => { if (!canceled) setHealth(value); })
      .catch((e: Error) => { if (!canceled) setError(e.message); });
    return () => { canceled = true; };
  }, [retry]);
  if (error) return <div role="alert"><p>Agent memory could not be loaded: {error}</p><Button variant="ghost" onClick={() => setRetry(n => n + 1)}>Try again</Button></div>;
  if (!health) return <p role="status">Loading agent memory…</p>;
  const now = Date.now();
  const count = (status: AgentMemoryHealth["status"]) => health.agents.filter(a => a.status === status).length;
  return <div className="rd-checkpoint-health">
    <p className="rd-checkpoint-health-summary">
      <span className="current">{count("current")} current</span> · <span className="behind">{count("behind")} behind</span> · <span className="failing">{count("failing")} failing</span> · <span className="none">{count("none")} no memory yet</span>
      {health.automatic !== null && <span className="rd-checkpoint-health-auto">Automatic checkpoints: {health.automatic ? "on" : "off"}</span>}
    </p>
    {health.agents.length === 0 ? <p>No agents were active in the last 7 days.</p> :
      <div className="rd-checkpoint-health-table"><table>
        <thead><tr><th>Status</th><th>Agent</th><th>Harness</th><th>Memory saved</th><th>Since then</th><th>Gaps</th></tr></thead>
        <tbody>{health.agents.map(a => <tr key={a.key}>
          <td><span className={`rd-health-chip ${a.status}`}>{STATUS[a.status]}</span></td>
          <td>{onOpenAgent ? <button className="rd-timeline-link" onClick={() => onOpenAgent(a.key)}>{a.name}</button> : a.name}</td>
          <td>{a.runtime === "codex" ? "Codex" : a.runtime === "claude-code" ? "Claude Code" : a.runtime ?? "—"}</td>
          <td>{ago(a.saved_at, now)}</td>
          <td>{since(a, now)}</td>
          <td>{a.gaps || ""}</td>
        </tr>)}</tbody>
      </table></div>}
    <p className="rd-checkpoint-health-note">Agents active in the last 7 days, problems first. Behind means new messages and memory older than 30 minutes. Select an agent to open its timeline.</p>
  </div>;
}
