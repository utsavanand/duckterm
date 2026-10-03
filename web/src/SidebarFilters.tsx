import { FilterGroup, SidebarFilterState, filterLabel, filterValue, hasFilters, matchesFilters, STATUS_FILTERS } from "./sidebarFilterState";
import { SessionView } from "./types";
import "./sidebarFilters.css";

const symbols: Record<string, string> = { busy: "●", waiting: "◷", idle: "○", stopped: "■", "claude-code": "C", codex: "⌘", copilot: "◉", opencode: "O", generic: "›_", local: "⌂", remote: "☁" };
export function SidebarFilters({ sessions, now, filters, onToggle, onClear, saveError }: {
  sessions: SessionView[]; now: number; filters: SidebarFilterState;
  onToggle: (group: FilterGroup, value: string) => void; onClear: () => void; saveError: boolean;
}) {
  const groups: { key: FilterGroup; label: string; values: string[] }[] = [
    { key: "status", label: "Status", values: STATUS_FILTERS },
    { key: "runtime", label: "Harness", values: [...new Set([...sessions.map(s => filterValue(s, "runtime", now)), ...filters.runtime])].sort() },
  ];
  if (filters.location.length || sessions.some(s => filterValue(s, "location", now) === "remote")) groups.push({ key: "location", label: "Location", values: ["local", "remote"] });
  const selected = (Object.keys(filters) as FilterGroup[]).flatMap(g => filters[g].map(v => filterLabel(g, v)));
  const count = sessions.filter(s => matchesFilters(s, filters, now)).length;
  return <section className="rd-sidebar-filters" aria-label="Session filters">
    {groups.map(group => <div className="rd-filter-group" role="group" aria-label={group.label} key={group.key}>
      <span className="rd-filter-group-label" aria-hidden="true">{group.label}</span>
      {group.values.map(value => {
        const label = filterLabel(group.key, value);
        // Faceted counts: apply the OTHER groups, then count this chip's value.
        const total = sessions.filter(s => matchesFilters(s, filters, now, group.key) && filterValue(s, group.key, now) === value).length;
        return <button key={value} type="button" className="rd-filter-chip" aria-label={`${label} ${total}`} aria-pressed={filters[group.key].includes(value)} title={`${label}: ${total}`} onClick={() => onToggle(group.key, value)}>
          <span aria-hidden="true">{symbols[value] ?? value.slice(0, 2)}</span><span className="rd-filter-chip-label" aria-hidden="true">{label}</span><span className="rd-filter-count" aria-hidden="true">{total}</span>
        </button>;
      })}
    </div>)}
    <div className="rd-filter-summary"><span role="status" title={selected.join(" · ")}>{hasFilters(filters) ? `${count} of ${sessions.length} · across all folders` : `${sessions.length} sessions · all folders`}</span>{hasFilters(filters) && <button type="button" onClick={onClear}>Clear filters</button>}</div>
    {saveError && <p className="rd-filter-storage" role="alert">Filters work here, but could not be saved for next time.</p>}
  </section>;
}
