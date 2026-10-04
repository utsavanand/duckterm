import { FilterGroup, SidebarFilterState, filterLabel, filterValue, hasFilters, matchesFilters, STATUS_FILTERS } from "./sidebarFilterState";
import { SessionView } from "./types";
import "./sidebarFilters.css";

const symbols: Record<string, string> = { busy: "●", waiting: "◷", idle: "○", stopped: "■", "claude-code": "C", codex: "⌘", copilot: "◉", opencode: "O", generic: "›_", local: "⌂", remote: "☁" };
export function SidebarFilterToggle({ filters, expanded, onToggle }: {
  filters: SidebarFilterState; expanded: boolean; onToggle: () => void;
}) {
  const count = Object.values(filters).reduce((total, values) => total + values.length, 0);
  return <button type="button" className="rd-filter-toggle" aria-expanded={expanded} aria-controls="rd-filter-options" aria-label={count ? `Filters, ${count} active` : "Filters"} onClick={onToggle}>
    Filters{count > 0 && <span className="rd-filter-badge" aria-hidden="true">{count}</span>}
  </button>;
}
export function SidebarFilters({ sessions, now, filters, onToggle, onClear, saveError, expanded }: {
  sessions: SessionView[]; now: number; filters: SidebarFilterState;
  expanded: boolean;
  onToggle: (group: FilterGroup, value: string) => void; onClear: () => void; saveError: boolean;
}) {
  const groups: { key: FilterGroup; label: string; values: string[] }[] = [
    { key: "status", label: "Status", values: STATUS_FILTERS },
    { key: "runtime", label: "Harness", values: [...new Set([...sessions.map(s => filterValue(s, "runtime", now)), ...filters.runtime])].sort() },
    { key: "location", label: "Location", values: ["local", "remote"] },
  ];
  const selected = (Object.keys(filters) as FilterGroup[]).flatMap(g => filters[g].map(v => filterLabel(g, v)));
  const count = sessions.filter(s => matchesFilters(s, filters, now)).length;
  const active = hasFilters(filters);
  return <section className="rd-sidebar-filters" aria-label="Session filters" hidden={!expanded && !active && !saveError}>
    <div id="rd-filter-options" className="rd-filter-options" hidden={!expanded}>
    {groups.map(group => <div className="rd-filter-group" role="group" aria-label={group.label} key={group.key}>
      <span className="rd-filter-group-label" aria-hidden="true">{group.label}</span>
      <div className="rd-filter-chips">
      {group.values.map(value => {
        const label = filterLabel(group.key, value);
        // Faceted counts: apply the OTHER groups, then count this chip's value.
        const total = sessions.filter(s => matchesFilters(s, filters, now, group.key) && filterValue(s, group.key, now) === value).length;
        return <button key={value} type="button" className="rd-filter-chip" aria-label={`${label} ${total}`} aria-pressed={filters[group.key].includes(value)} title={`${label}: ${total}`} onClick={() => onToggle(group.key, value)}>
          <span aria-hidden="true">{symbols[value] ?? value.slice(0, 2)}</span><span className="rd-filter-chip-label" aria-hidden="true">{label}</span><span className="rd-filter-count" aria-hidden="true">{total}</span>
        </button>;
      })}</div>
    </div>)}
    </div>
    {(expanded || active) && <div className="rd-filter-summary"><span role="status" title={selected.join(" · ")}>{active ? `${count} of ${sessions.length} · across all folders` : `${sessions.length} sessions · all folders`}</span>{active && <button type="button" onClick={onClear}>Clear filters</button>}</div>}
    {saveError && <p className="rd-filter-storage" role="alert">Filters work here, but could not be saved for next time.</p>}
  </section>;
}
