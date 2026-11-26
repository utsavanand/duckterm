import { createContext, PointerEvent as ReactPointerEvent, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import { AnalyticsTab, fillDays, groupTokens, loadAnalytics, sumValues, TokenAnalytics, windowStart } from "./analyticsData";
import { api, PrRow } from "./api";
import { AutopilotSummary } from "./autopilotApi";
import { useEdition } from "./edition";
import { adjust, appended, Box, boxOf, COLUMNS, homeDefaults, inReadingOrder } from "./homeGrid";
import { HomeChat } from "./HomeChat";
import { dollars, money } from "./pricing";
import { oracleWidgets } from "./OracleWidgets";
import { useRelay } from "./relay";
import { ago, compact, TowerAgent, teams } from "./tower";
import { Layout, requestLayout, WidgetContent, WidgetInstance, WidgetType } from "./Widgets";
import { WidgetStreams } from "./widgetStreams";
import "./oracleWidgets.css";
import "./home.css";

type Relay = ReturnType<typeof useRelay>;
// Autopilot is null where the edition or the `autopilot` flag has no view to open.
type Autopilot = { summary: AutopilotSummary | null; open: () => void } | null;
type Actions = { agents: TowerAgent[]; relay: Relay | null; now: number; autopilot: Autopilot; onTeams: () => void; onOpenTerminal: (key: string) => void; onAnalytics: (tab: AnalyticsTab) => void };
const HomeActions = createContext<Actions>({ agents: [], relay: null, now: 0, autopilot: null, onTeams: () => undefined, onOpenTerminal: () => undefined, onAnalytics: () => undefined });
const CATALOG_GROUPS = ["Navigate", "Core", "Pulse", "Teams"];
const CATALOG: Record<string, { group: string; description: string }> = {
  teams: { group: "Navigate", description: "Your sessions and folders. Opens the current dashboard." },
  autopilot: { group: "Navigate", description: "Your software factory. Opens the factory view." },
  chat: { group: "Core", description: "Ask about anything your teams or factory are doing." },
  prs: { group: "Pulse", description: "Open pull requests across teams, with CI and review state." },
  spend: { group: "Pulse", description: "Today, the last 7 days, and the split between Teams and Autopilot." },
  "needs-you": { group: "Pulse", description: "Approvals and questions waiting on you." },
  usage: { group: "Pulse", description: "Tokens over the last 7 days, by harness." },
  agents: { group: "Teams", description: "Every session by state, across teams." },
  mail: { group: "Teams", description: "Messages agents sent each other in the last day." },
  backup: { group: "Teams", description: "When the last backup finished." },
  remote: { group: "Teams", description: "Sessions running on the remote workspace." },
};
// Widgets whose header carries no title: the tile itself names the destination.
const NAV = new Set(["teams", "autopilot"]);
// One request per top-level folder, each running git across its sessions: poll gently.
const PR_POLL_MS = 5 * 60_000;

// The page DuckTerm opens on (docs/feature-flags.md, `home`): one chat over
// everything, the ways into Teams and Autopilot, and fleet widgets on a
// 12-column grid. Oracle's chat and Needs you fold in here.
export function Home({ agents, now, autopilot = null, onTeams, onOpenTerminal, onAnalytics }: {
  agents: TowerAgent[]; now: number; autopilot?: Autopilot; onTeams: () => void; onOpenTerminal: (key: string) => void; onAnalytics: (tab: AnalyticsTab) => void;
}) {
  const { has } = useEdition();
  const oracle = has("oracle"), remote = oracle && has("remote_workspaces"), prs = has("pr_review");
  const relay = useRelay(oracle);
  const [source] = useState(() => new WidgetStreams());
  const analytics = useRef(onAnalytics); analytics.current = onAnalytics;
  const registry = useMemo(() => homeWidgets({ oracle, remote, prs, onAnalytics: tab => analytics.current(tab) }), [oracle, remote, prs]);
  useEffect(() => { source.emit("sessions", { status: "ready", value: agents }); }, [source, agents]);
  useEffect(() => {
    source.emit("needs-you", relay.loaded ? { status: "ready", value: { notes: relay.notes.filter(n => n.status === "open" && n.urgency !== "offer").sort((a, b) => a.created_at - b.created_at), now } } : { status: "loading" });
  }, [source, relay.notes, relay.loaded, now]);
  const actions = useMemo(() => ({ agents, relay: oracle ? relay : null, now, autopilot, onTeams, onOpenTerminal, onAnalytics: (tab: AnalyticsTab) => analytics.current(tab) }), [agents, oracle, relay, now, autopilot, onTeams, onOpenTerminal]);
  return <HomeActions.Provider value={actions}>
    <div className="rd-home"><HomeGrid registry={registry} source={source} /></div>
  </HomeActions.Provider>;
}

function homeWidgets({ oracle, remote, prs, onAnalytics }: { oracle: boolean; remote: boolean; prs: boolean; onAnalytics: (tab: AnalyticsTab) => void }): WidgetType[] {
  // Home's own Needs you and Usage replace the tower's tiles of the same name.
  const fleet = oracleWidgets(onAnalytics, { oracle, remote })
    .filter(widget => !["tokens", "folder-tasks", ...(oracle ? ["needs-you"] : [])].includes(widget.type))
    .map(widget => ({ ...widget, slots: ["home" as const] }));
  return [
    { type: "teams", title: "Teams", slots: ["home"], streams: ["sessions"], render: ({ streams }) => <TeamsTile agents={streams.sessions.status === "ready" ? streams.sessions.value as TowerAgent[] : []} /> },
    { type: "autopilot", title: "Autopilot", slots: ["home"], streams: [], render: AutopilotTile },
    ...(oracle ? [
      { type: "chat", title: "Ask DuckTerm", slots: ["home" as const], streams: [], render: ChatWidget },
      { type: "needs-you", title: "Needs you", slots: ["home" as const], streams: [], render: NeedsYouList },
    ] : []),
    ...(prs ? [{ type: "prs", title: "Pull requests", slots: ["home" as const], streams: [], render: PrsWidget }] : []),
    { type: "spend", title: "Spend", slots: ["home"], streams: [], render: SpendWidget },
    { type: "usage", title: "Usage · 7 days", slots: ["home"], streams: [], render: UsageWidget },
    ...fleet,
  ];
}

function TeamsTile({ agents }: { agents: TowerAgent[] }) {
  const { onTeams } = useContext(HomeActions);
  const count = (state: string) => agents.filter(a => a.shownState === state).length;
  const folders = teams(agents).filter(team => team.name !== "No folder").length;
  return <button className="rd-home-nav" onClick={onTeams}>
    <span className="rd-home-nav-title">Teams <span aria-hidden="true">→</span></span>
    <span className="rd-home-nav-sub">{folders} {folders === 1 ? "folder" : "folders"} · {agents.length} {agents.length === 1 ? "session" : "sessions"}</span>
    <span className="rd-home-chips">
      <span className={`rd-home-chip${count("waiting") ? " wait" : ""}`}>{count("waiting")} need you</span>
      <span className="rd-home-chip busy">{count("busy")} working</span>
      <span className="rd-home-chip">{count("idle")} idle</span>
    </span>
  </button>;
}

function AutopilotTile() {
  const { autopilot } = useContext(HomeActions);
  if (!autopilot) return <div className="rd-home-nav" aria-disabled="true">
    <span className="rd-home-nav-title">Autopilot</span>
    <span className="rd-home-nav-sub">The software factory: skill-kit flows run unattended on remote workers.</span>
    <span className="rd-home-chips"><span className="rd-home-chip">Not available yet</span></span>
  </div>;
  // Waiting at a gate is the only count that asks the owner to act, so it leads.
  // A failed summary shows no counts rather than zeros.
  const s = autopilot.summary;
  return <button className="rd-home-nav" onClick={autopilot.open}>
    <span className="rd-home-nav-title">Autopilot <span aria-hidden="true">→</span></span>
    {/* One line, so four chips fit the tile's default two rows. */}
    <span className="rd-home-nav-sub">Skill-kit flows running unattended</span>
    {s && <span className="rd-home-chips">
      <span className={`rd-home-chip${s.waiting_gate ? " wait" : ""}`}>{s.waiting_gate} need you</span>
      <span className="rd-home-chip busy">{s.active} running</span>
      <span className="rd-home-chip">{s.done_today} done today</span>
      <span className={`rd-home-chip${s.failed_today ? " bad" : ""}`}>{s.failed_today} failed</span>
    </span>}
  </button>;
}

function ChatWidget() {
  const { agents, onOpenTerminal } = useContext(HomeActions);
  const sessions = useMemo(() => agents.map(a => ({ key: a.key, name: a.label })), [agents]);
  return <HomeChat sessions={sessions} onOpenTerminal={onOpenTerminal} />;
}

// Agents' questions to the owner, oldest first; each opens its terminal.
function NeedsYouList() {
  const { relay, now, onOpenTerminal } = useContext(HomeActions);
  if (!relay?.loaded) return <div className="rd-widget-unavailable" role="status">Loading…</div>;
  const notes = relay.notes.filter(n => n.status === "open" && n.urgency !== "offer").sort((a, b) => a.created_at - b.created_at);
  if (!notes.length) return <p className="rd-home-empty">Nothing needs you.</p>;
  return <>
    <ul className="rd-home-rows">{notes.slice(0, 8).map(note => <li key={note.id}>
      <span className="rd-home-dot wait" aria-hidden="true" />
      <span className="rd-home-row-main">
        <button className="rd-home-row-link" title={`Open ${note.name}'s terminal`} onClick={() => onOpenTerminal(note.session_key)}>{note.name}: {note.question ?? note.tool ?? note.kind}</button>
        <small>{note.folder ? `${note.folder.split("/")[0]} · ` : ""}{ago(now - note.created_at)} ago</small>
      </span>
    </li>)}</ul>
    {notes.length > 8 && <p className="rd-home-empty">and {notes.length - 8} more</p>}
  </>;
}

const AGENT_NAMES: Record<string, string> = { "claude-code": "Claude Code", codex: "Codex" };
const AGENT_COLORS = ["var(--brand-green)", "var(--busy)", "var(--wait)"];

function useWeekOfTokens() {
  const [data, setData] = useState<TokenAnalytics | null>(null), [error, setError] = useState("");
  useEffect(() => {
    const controller = new AbortController();
    loadAnalytics<TokenAnalytics>("tokens", new URLSearchParams({ days: "7" }), controller.signal)
      .then(setData, (e: Error) => { if (!controller.signal.aborted) setError(e.message); });
    return () => controller.abort();
  }, []);
  return { data, error };
}

function weekDays(data: TokenAnalytics): string[] {
  return fillDays([], windowStart("7", data.today, data.earliest_day ?? data.today), data.today, 0).map(row => row.label);
}

function Sparkline({ values }: { values: number[] }) {
  const peak = Math.max(1e-9, ...values);
  const points = values.map((v, i) => `${values.length > 1 ? (i / (values.length - 1)) * 140 : 70},${44 - (v / peak) * 40}`).join(" ");
  return <svg className="rd-home-spark" viewBox="0 0 140 46" preserveAspectRatio="none" aria-hidden="true"><polyline points={points} /></svg>;
}

function Split({ parts, format }: { parts: { name: string; value: number }[]; format: (n: number) => string }) {
  const total = parts.reduce((n, p) => n + p.value, 0);
  return <>
    <span className="rd-home-split" aria-hidden="true">{parts.map((p, i) => <i key={p.name} style={{ width: `${(p.value / Math.max(1e-9, total)) * 100}%`, background: AGENT_COLORS[i % AGENT_COLORS.length] }} />)}</span>
    <span className="rd-home-legend">{parts.map((p, i) => <span key={p.name}><i style={{ background: AGENT_COLORS[i % AGENT_COLORS.length] }} />{p.name} <b>{format(p.value)}</b></span>)}</span>
  </>;
}

function UsageWidget() {
  const { onAnalytics } = useContext(HomeActions);
  const { data, error } = useWeekOfTokens();
  if (error) return <div className="rd-widget-unavailable"><strong>Unavailable</strong><p>{error}</p></div>;
  if (!data) return <div className="rd-widget-unavailable" role="status">Loading…</div>;
  const byDay = new Map(groupTokens(data.rows, "day").map(row => [row.label, sumValues(row)]));
  const days = weekDays(data).map(day => byDay.get(day) ?? 0);
  const agents = groupTokens(data.rows, "agent").map(row => ({ name: AGENT_NAMES[row.label] ?? row.label, value: sumValues(row) }));
  return <button className="rd-home-usage" onClick={() => onAnalytics("tokens")} aria-label="Open token analytics">
    <span className="rd-home-kpi">{compact(days.reduce((n, v) => n + v, 0))}<small>tokens</small></span>
    <Sparkline values={days} />
    <Split parts={agents} format={compact} />
    <span className="rd-home-legend">Today <b>{compact(days.at(-1) ?? 0)}</b></span>
  </button>;
}

// Dollars at API list prices (pricing.ts), split the way the work is run:
// Teams today, Autopilot once the factory reports its own usage.
function SpendWidget() {
  const { onAnalytics } = useContext(HomeActions);
  const { data, error } = useWeekOfTokens();
  if (error) return <div className="rd-widget-unavailable"><strong>Unavailable</strong><p>{error}</p></div>;
  if (!data) return <div className="rd-widget-unavailable" role="status">Loading…</div>;
  const byDay = new Map<string, number>(), byAgent = new Map<string, number>();
  for (const row of data.rows) {
    const cost = dollars(row);
    byDay.set(row.day, (byDay.get(row.day) ?? 0) + cost);
    const agent = AGENT_NAMES[row.agent] ?? row.agent;
    byAgent.set(agent, (byAgent.get(agent) ?? 0) + cost);
  }
  const days = weekDays(data).map(day => byDay.get(day) ?? 0), week = days.reduce((n, v) => n + v, 0);
  return <button className="rd-home-usage" onClick={() => onAnalytics("tokens")} aria-label="Open spend analytics"
    title={`${[...byAgent].map(([name, value]) => `${name} ${money(value)}`).join(" · ")} over 7 days. Estimated at API list prices per token, including for subscription use; Codex models use GPT-5 rates.`}>
    <span className="rd-home-kpi">{money(days.at(-1) ?? 0)}<small>today</small></span>
    <Sparkline values={days} />
    <Split parts={[{ name: "Teams", value: week }, { name: "Autopilot", value: 0 }]} format={money} />
    <span className="rd-home-legend">7 days <b>{money(week)}</b></span>
  </button>;
}

function prDot(row: PrRow): string {
  if (row.needs_you) return "wait";
  if (row.status === "merged") return "done";
  if (row.checks?.failed) return "bad";
  if (row.checks?.pending) return "busy";
  return "ok";
}

// Open and waiting PRs across every top-level folder, needs-you first.
function PrsWidget() {
  const { agents, onOpenTerminal } = useContext(HomeActions);
  const folders = useMemo(() => [...new Set(agents.map(a => a.group?.split("/")[0]).filter((f): f is string => !!f))].sort(), [agents]);
  const key = folders.join("\n");
  const [rows, setRows] = useState<PrRow[] | null>(null), [error, setError] = useState(""), [notice, setNotice] = useState("");
  useEffect(() => {
    let stopped = false;
    setRows(null);
    let timer: ReturnType<typeof setTimeout> | undefined;
    const load = async () => {
      const results = await Promise.allSettled(key.split("\n").filter(Boolean).map(folder => api.folderPrs(folder)));
      if (stopped) return;
      const seen = new Set<string>(), merged: PrRow[] = [], problems: string[] = [];
      for (const result of results) if (result.status === "fulfilled") {
        for (const row of result.value.rows) {
          const id = `${row.repo}\n${row.branch}`;
          if (!seen.has(id)) { seen.add(id); merged.push(row); }
        }
        for (const repo of result.value.repos) if (repo.error) problems.push(`${repo.name}: ${repo.error}`);
      }
      // Without GitHub there are branches but no PRs; say why rather than "none".
      setNotice(problems[0] ?? "");
      const failed = results.find((r): r is PromiseRejectedResult => r.status === "rejected");
      setError(failed && !merged.length ? (failed.reason as Error).message : "");
      setRows(merged.filter(r => r.needs_you || r.pr?.state === "open")
        .sort((a, b) => Number(b.needs_you) - Number(a.needs_you) || (b.updated_at ?? 0) - (a.updated_at ?? 0)));
      timer = setTimeout(() => void load(), PR_POLL_MS);
    };
    void load();
    return () => { stopped = true; clearTimeout(timer); };
  }, [key]);
  if (error) return <div className="rd-widget-unavailable"><strong>Unavailable</strong><p>{error}</p></div>;
  if (!folders.length) return <p className="rd-home-empty">No sessions in folders yet.</p>;
  if (!rows) return <div className="rd-widget-unavailable" role="status">Loading…</div>;
  if (!rows.length) return <p className="rd-home-empty">{notice || "No open pull requests."}</p>;
  return <ul className="rd-home-rows">{rows.map(row => <li key={`${row.repo}\n${row.branch}`}>
    <span className={`rd-home-dot ${prDot(row)}`} aria-hidden="true" />
    {row.pr && <span className="rd-home-num">#{row.pr.number}</span>}
    <span className="rd-home-row-main">
      {row.pr ? <a href={row.pr.url} target="_blank" rel="noreferrer">{row.pr.title}</a> : <span>{row.branch}</span>}
      <small>{row.label} · Teams{row.session && <> · <button onClick={() => onOpenTerminal(row.session!.key)}>{row.session.name}</button></>}</small>
    </span>
  </li>)}</ul>;
}

// origin is the layout when the drag began, so passing over a widget and back leaves it in place.
type Drag = { id: string; mode: "move" | "resize"; x: number; y: number; start: Box; origin: WidgetInstance[]; cell: { w: number; h: number } };

function HomeGrid({ registry, source }: { registry: WidgetType[]; source: WidgetStreams }) {
  const defaults = useMemo(() => homeDefaults().filter(item => registry.some(type => type.type === item.type)), [registry]);
  const [layout, setLayout] = useState<Layout>({ instances: defaults, revision: "default" });
  const [loading, setLoading] = useState(true), [error, setError] = useState(""), [editing, setEditing] = useState(false), [adding, setAdding] = useState(false);
  const grid = useRef<HTMLDivElement>(null), drag = useRef<Drag | null>(null), [dragging, setDragging] = useState<string | null>(null);
  const revision = useRef("default"), pending = useRef<WidgetInstance[] | null>(null), saving = useRef(false), active = useRef(true);
  const load = useCallback(async () => {
    setLoading(true); setError("");
    try { const value = await requestLayout("home"); if (active.current) { revision.current = value.revision; setLayout(value); } }
    catch (e) { if (active.current) setError((e as Error).message); }
    finally { if (active.current) setLoading(false); }
  }, []);
  useEffect(() => { active.current = true; void load(); return () => { active.current = false; }; }, [load]);

  // Layout changes apply at once; saves run one at a time, and the newest
  // pending layout is the one sent next.
  const flush = useCallback(async () => {
    if (saving.current || !pending.current) return;
    const instances = pending.current; pending.current = null; saving.current = true;
    try {
      const saved = await requestLayout("home", { revision: revision.current, instances: inReadingOrder(instances).map((item, position) => ({ ...item, position })) });
      revision.current = saved.revision;
    } catch (e) { if (active.current) setError((e as Error).message); }
    finally { saving.current = false; void flush(); }
  }, []);
  const commit = useCallback((instances: WidgetInstance[]) => {
    setLayout(previous => ({ ...previous, instances })); pending.current = instances; void flush();
  }, [flush]);

  const start = (event: ReactPointerEvent, id: string, mode: Drag["mode"]) => {
    if (!editing || !grid.current || event.button !== 0) return;
    const item = layout.instances.find(i => i.id === id);
    if (!item) return;
    event.preventDefault();
    const style = getComputedStyle(grid.current), gap = parseFloat(style.columnGap) || 0;
    const width = (grid.current.getBoundingClientRect().width - gap * (COLUMNS - 1)) / COLUMNS;
    drag.current = { id, mode, x: event.clientX, y: event.clientY, start: boxOf(item), origin: layout.instances, cell: { w: width + gap, h: (parseFloat(style.gridAutoRows) || 58) + gap } };
    (event.currentTarget as HTMLElement).setPointerCapture(event.pointerId);
    setDragging(id);
  };
  const move = (event: ReactPointerEvent) => {
    const d = drag.current;
    if (!d) return;
    const dx = Math.round((event.clientX - d.x) / d.cell.w), dy = Math.round((event.clientY - d.y) / d.cell.h);
    setLayout(previous => ({ ...previous, instances: adjust(d.origin, d.id, d.start, d.mode, dx, dy) }));
  };
  const end = () => {
    if (!drag.current) return;
    drag.current = null; setDragging(null);
    commit(current.current);
  };
  const nudge = (event: React.KeyboardEvent, item: WidgetInstance) => {
    const step: Record<string, [number, number]> = { ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1] };
    const delta = step[event.key];
    if (!delta) return;
    event.preventDefault();
    commit(adjust(layout.instances, item.id, boxOf(item), event.shiftKey ? "resize" : "move", delta[0], delta[1]));
  };

  const current = useRef(layout.instances); current.current = layout.instances;
  const shown = inReadingOrder(layout.instances).filter(item => registry.some(type => type.type === item.type));
  const missing = registry.filter(type => !layout.instances.some(item => item.type === type.type));
  return <section className="rd-home-wrap" aria-label="Home">
    <header className="rd-home-bar">
      <h1>Home</h1>
      <span className="rd-spacer" />
      {editing && <>
        <button className="rd-btn rd-btn-ghost rd-btn-sm" disabled={loading} onClick={() => commit(defaults)}>Reset layout</button>
        <span className="rd-home-add">
          <button className="rd-btn rd-btn-ghost rd-btn-sm" aria-expanded={adding} disabled={loading} onClick={() => setAdding(!adding)}>+ Add widget</button>
          {adding && <span className="rd-widget-add rd-home-catalog" role="group" aria-label="Add a widget">{CATALOG_GROUPS.map(group => {
            const types = registry.filter(type => CATALOG[type.type]?.group === group);
            return types.length > 0 && <span key={group} className="rd-home-catalog-group"><span className="rd-home-catalog-name">{group}</span>
              {types.map(type => {
                const placed = !missing.includes(type);
                return <button key={type.type} disabled={placed} onClick={() => { setAdding(false); commit(appended(layout.instances, type.type)); }}>
                  {type.title}{placed && " · on page"}<small>{CATALOG[type.type].description}</small>
                </button>;
              })}</span>;
          })}</span>}
        </span>
      </>}
      <button className={`rd-btn rd-btn-ghost rd-btn-sm${editing ? " rd-btn-active" : ""}`} aria-pressed={editing} disabled={loading} onClick={() => { setEditing(!editing); setAdding(false); }}>{editing ? "Done" : "Edit layout"}</button>
    </header>
    {error && <p className="rd-widget-error" role="alert">{error} <button className="rd-btn rd-btn-ghost rd-btn-sm" onClick={() => void load()}>Reload layout</button></p>}
    <div ref={grid} className={`rd-home-grid${editing ? " editing" : ""}`} onPointerMove={move} onPointerUp={end} onPointerCancel={end}>
      {shown.map(item => {
        const type = registry.find(t => t.type === item.type)!, b = boxOf(item);
        return <article key={item.id} aria-label={type.title} className={`rd-home-widget rd-home-${item.type}${dragging === item.id ? " dragging" : ""}`}
          style={{ gridColumn: `${b.x + 1} / span ${b.w}`, gridRow: `${b.y + 1} / span ${b.h}` }}>
          {(editing || !NAV.has(item.type)) && <header onPointerDown={event => start(event, item.id, "move")}>
            {!NAV.has(item.type) && <h3>{type.title}</h3>}
            {editing && <span className="rd-home-tools">
              <button aria-label={`Move ${type.title}`} title="Drag, or use the arrow keys. Shift+arrows resize." onKeyDown={event => nudge(event, item)}>⠿</button>
              <button aria-label={`Remove ${type.title}`} onPointerDown={event => event.stopPropagation()} onClick={() => commit(layout.instances.filter(i => i.id !== item.id))}>✕</button>
            </span>}
          </header>}
          <div className="rd-home-body"><WidgetContent type={type} instance={item} source={source} /></div>
          {editing && <span className="rd-home-resize" aria-hidden="true" onPointerDown={event => start(event, item.id, "resize")} />}
        </article>;
      })}
    </div>
  </section>;
}
