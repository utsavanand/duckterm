import { SessionView, SessionState } from "./types";

export type TowerAgent = SessionView & { shownState: SessionState };

const STALE_BUSY_MS = 30 * 60_000;

// Busy with no event for 30 minutes: the badge is probably stale, or the
// agent is stuck. The Oracle design's "stale state" rule.
export function looksStuck(a: TowerAgent, now: number): boolean {
  return a.shownState === "busy" && now - a.updatedAt > STALE_BUSY_MS;
}

export function teamOf(a: SessionView): string {
  return a.group ? a.group.split("/")[0] : "No folder";
}

export function teams(agents: TowerAgent[]): { name: string; agents: TowerAgent[] }[] {
  const byTeam = new Map<string, TowerAgent[]>();
  for (const a of agents) byTeam.set(teamOf(a), [...(byTeam.get(teamOf(a)) ?? []), a]);
  return [...byTeam.entries()]
    .map(([name, members]) => ({ name, agents: members }))
    .sort((x, y) => y.agents.length - x.agents.length || x.name.localeCompare(y.name));
}

export function runningSubagents(a: SessionView): number {
  return (a.subagents ?? []).filter((s) => s.state === "running").length;
}

export interface Fact {
  label: string;
  value: string;
}

// Only facts that are true right now; an empty fleet has none.
export function facts(agents: TowerAgent[], now: number): Fact[] {
  const out: Fact[] = [];
  const all = teams(agents);
  if (all.length > 1) out.push({ label: "Busiest team", value: `${all[0].name}, ${all[0].agents.length} agents` });
  const ctx = [...agents].filter((a) => a.contextTokens).sort((x, y) => (y.contextTokens ?? 0) - (x.contextTokens ?? 0))[0];
  if (ctx) out.push({ label: "Largest context", value: `${ctx.label}, ${Math.round((ctx.contextTokens ?? 0) / 1000)}k tokens` });
  const subs = [...agents].sort((x, y) => runningSubagents(y) - runningSubagents(x))[0];
  if (subs && runningSubagents(subs) > 0) out.push({ label: "Most sub-agents", value: `${subs.label}, ${runningSubagents(subs)}` });
  const stuck = agents.filter((a) => looksStuck(a, now));
  if (stuck.length) out.push({ label: "Looks stuck", value: `${stuck[0].label} (${teamOf(stuck[0])}), busy with no activity for ${ago(now - stuck[0].updatedAt)}` + (stuck.length > 1 ? `, and ${stuck.length - 1} more` : "") });
  return out;
}

export function ago(ms: number): string {
  const m = Math.max(0, Math.round(ms / 60_000));
  if (m < 1) return "under a minute";
  if (m < 60) return `${m} min`;
  if (m < 1440) return `${Math.round(m / 60)} h`;
  const d = Math.round(m / 1440);
  return `${d} day${d === 1 ? "" : "s"}`;
}

export const ROW_PX = 88;

// Stable jitter from the session key, so the scene looks the same each load.
function unit(seed: string): number {
  let h = 2166136261;
  for (const c of seed) h = Math.imul(h ^ c.charCodeAt(0), 16777619);
  return (h >>> 0) / 4294967295;
}

// A loose grid per team: columns by team size, one 88px row per line of
// ducks, jitter small enough that neighbors never overlap.
export function placement(keys: string[]): { rows: number; spots: { left: number; top: number; drift: number }[] } {
  const cols = Math.max(1, Math.min(keys.length, keys.length > 4 ? 4 : 3));
  const rows = Math.max(1, Math.ceil(keys.length / cols));
  const spots = keys.map((key, i) => {
    const j = unit(key);
    return {
      left: ((i % cols) + 0.5) / cols * 100 + (j - 0.5) * (24 / cols),
      top: 16 + Math.floor(i / cols) * ROW_PX + (j - 0.5) * 12,
      drift: Math.round((j - 0.5) * 36),
    };
  });
  return { rows, spots };
}

export function compact(n: number): string {
  if (n >= 1e9) return `${(n / 1e9).toFixed(2)}B`;
  if (n >= 1e6) return `${(n / 1e6).toFixed(1)}M`;
  if (n >= 1e3) return `${Math.round(n / 1e3)}k`;
  return String(n);
}
