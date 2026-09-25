import { SubAgent } from "./types";

export function HelperAgents({ agents, sessionKey }: { agents: SubAgent[]; sessionKey: string }) {
  const active = agents.filter((agent) => agent.state !== "done");
  const completed = agents.filter((agent) => agent.state === "done");
  const rows = (items: SubAgent[]) => <ul className="rd-subagents">{items.map((agent) => <li key={agent.agent_id} className={`rd-subagent st-${agent.state}`} title={agent.agent_prompt ?? undefined}>
    <span className="rd-subagent-twig">↳</span>
    <span className={`dot ${agent.state === "running" ? "on" : ""}`} />
    <span className="rd-subagent-type">{agent.agent_type ?? "subagent"}</span>
    {agent.agent_prompt && <span className="rd-subagent-task">{agent.agent_prompt}</span>}
  </li>)}</ul>;
  return <>
    {active.length > 0 && rows(active)}
    {completed.length > 0 && <details key={sessionKey} className="rd-completed-helpers">
      <summary aria-label={`${completed.length} completed ${completed.length === 1 ? "helper" : "helpers"}`}>{completed.length} completed</summary>
      {rows(completed)}
    </details>}
  </>;
}
