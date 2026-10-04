import { useEffect, useState } from "react";
import { effectiveState } from "./sessions";
import { SessionView } from "./types";

export type FilterGroup = "status" | "runtime" | "location";
export type SidebarFilterState = Record<FilterGroup, string[]>;
export const FILTER_KEY = "rd.sidebarFilters";
export const FILTER_PANEL_KEY = "rd.sidebarFiltersExpanded";
export const EMPTY_FILTERS: SidebarFilterState = { status: [], runtime: [], location: [] };
export const STATUS_FILTERS = ["busy", "waiting", "idle", "stopped"];
const RUNTIME_LABELS: Record<string, string> = { "claude-code": "Claude Code", codex: "Codex", copilot: "Copilot", opencode: "OpenCode", generic: "Shell" };
export function filterLabel(group: FilterGroup, value: string): string {
  if (group === "runtime") return RUNTIME_LABELS[value] ?? value;
  return ({ busy: "Working", waiting: "Waiting", idle: "Idle", stopped: "Stopped", local: "Local", remote: "Remote" } as Record<string, string>)[value] ?? value;
}
export function sidebarSessions(sessions: SessionView[]): SessionView[] {
  return sessions.filter(s => !["archived", "merged"].includes(s.state));
}
export function filterValue(session: SessionView, group: FilterGroup, now: number): string {
  if (group === "runtime") return session.runtime || "generic";
  if (group === "location") return !session.host || session.host === "local" ? "local" : "remote";
  const state = effectiveState(session, now);
  return ["stopped", "interrupted", "terminated"].includes(state) ? "stopped" : state;
}
export function matchesFilters(session: SessionView, filters: SidebarFilterState, now: number, except?: FilterGroup): boolean {
  if (["archived", "merged"].includes(session.state)) return false;
  return (Object.keys(filters) as FilterGroup[]).every(group => group === except || !filters[group].length || filters[group].includes(filterValue(session, group, now)));
}
export function hasFilters(filters: SidebarFilterState): boolean {
  return Object.values(filters).some(values => values.length > 0);
}
export function readFilters(value: string | null): SidebarFilterState {
  try {
    const raw = JSON.parse(value || "{}");
    const values = (key: FilterGroup) => Array.isArray(raw?.[key]) ? [...new Set<string>(raw[key].filter((v: unknown) => typeof v === "string" && v.length > 0 && v.length <= 128))].slice(0, 32) : [];
    return { status: values("status").filter(v => STATUS_FILTERS.includes(v)), runtime: values("runtime"), location: values("location").filter(v => ["local", "remote"].includes(v)) };
  } catch { return EMPTY_FILTERS; }
}
export function useSidebarFilters() {
  const [expanded, setExpanded] = useState(() => {
    try { return localStorage.getItem(FILTER_PANEL_KEY) === "true"; } catch { return false; }
  });
  const [filters, setFilters] = useState<SidebarFilterState>(() => {
    try { return readFilters(localStorage.getItem(FILTER_KEY)); } catch { return EMPTY_FILTERS; }
  });
  const [saveError, setSaveError] = useState(false);
  useEffect(() => {
    try {
      const value = JSON.stringify(filters);
      localStorage.setItem(FILTER_KEY, value);
      localStorage.setItem(FILTER_PANEL_KEY, String(expanded));
      setSaveError(localStorage.getItem(FILTER_KEY) !== value || localStorage.getItem(FILTER_PANEL_KEY) !== String(expanded));
    } catch { setSaveError(true); }
  }, [filters, expanded]);
  const toggle = (group: FilterGroup, value: string) => setFilters(current => ({ ...current,
    [group]: current[group].includes(value) ? current[group].filter(v => v !== value) : [...current[group], value],
  }));
  return { filters, toggle, clear: () => setFilters(EMPTY_FILTERS), saveError,
    expanded, toggleExpanded: () => setExpanded(current => !current) };
}
export type SidebarFilterControls = ReturnType<typeof useSidebarFilters>;
