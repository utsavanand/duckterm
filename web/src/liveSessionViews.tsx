import { ComponentProps } from "react";
import { AgentTree } from "./AgentTree";
import { ControlTower } from "./ControlTower";
import { ContextPanel } from "./ContextPanel";
import { InboxView } from "./InboxView";
import { SessionCard } from "./SessionCard";
import { effectiveState } from "./sessions";
import { SessionView } from "./types";
import { useNow } from "./useNow";

// These boundaries keep idle settling and relative ages live without ticking
// the terminal, its ancestors, session selection or the rest of the dashboard.
export function LiveAgentTree({ active, ...props }: Omit<ComponentProps<typeof AgentTree>, "now"> & { active: boolean }) {
  const now = useNow(1000, active);
  return <AgentTree {...props} now={now} />;
}
export function LiveControlTower({ agents, ...props }: Omit<ComponentProps<typeof ControlTower>, "now" | "agents"> & { agents: SessionView[] }) {
  const now = useNow();
  return <ControlTower {...props} now={now} agents={agents.map(s => ({ ...s, shownState: effectiveState(s, now) }))} />;
}
export function LiveSessionCard({ active, ...props }: Omit<ComponentProps<typeof SessionCard>, "now"> & { active: boolean }) {
  const now = useNow(1000, active);
  return <SessionCard {...props} now={now} />;
}
export function LiveContextPanel({ active, ...props }: ComponentProps<typeof ContextPanel> & { active: boolean }) {
  useNow(1000, active);
  return <ContextPanel {...props} active={active} />;
}
export function LiveInboxView({ session, active }: { session: SessionView; active: boolean }) {
  const now = useNow(1000, active);
  return <InboxView session={{ ...session, state: effectiveState(session, now) }} />;
}
