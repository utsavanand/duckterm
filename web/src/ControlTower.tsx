import { tasksWidget } from "./FolderTasks";
import { ReactNode, useEffect, useMemo, useRef, useState } from "react";
import { useEdition } from "./edition";
import { AnalyticsTab } from "./analyticsData";
import { api } from "./api";
import { Duck, poseFor } from "./Duck";
import { SessionLocationDuck, sessionLocation } from "./SessionLocationDuck";
import { OracleChat } from "./OracleChat";
import { NoteCard } from "./RelayNotes";
import { FleetArrows } from "./FleetArrows";
import { FleetPulse } from "./FleetPulse";
import { useRelay } from "./relay";
import {
  TowerAgent,
  ago,
  compact,
  facts,
  looksStuck,
  pods,
  runningSubagents,
  teamOf,
  teams,
} from "./tower";

import { Widgets } from "./Widgets";
import { WidgetStreams } from "./widgetStreams";
import { oracleWidgets } from "./OracleWidgets";
import "./oracleWidgets.css";

type Mode = "inbox" | "prompt";

const STATE_WORD: Record<string, string> = {
  busy: "Busy",
  idle: "Idle",
  waiting: "Waiting on you",
  stopped: "Stopped",
  interrupted: "Interrupted",
  terminated: "Ended",
};

// Factual fleet insights, every agent grouped by team (top-level folder), and
// a way to message one agent. Oracle's chat and relay are added when available.
// Session states come from the dashboard's own stream; /control-tower adds
// what that stream lacks (tokens, mail, backup, remote).
export function ControlTower({
  agents,
  now,
  onBack,
  onOpenTerminal,
  onAnalytics,
}: {
  agents: TowerAgent[];
  now: number;
  onBack: () => void;
  onOpenTerminal: (key: string) => void;
  onAnalytics: (tab: AnalyticsTab) => void;
}) {
  const { has } = useEdition();
  const overview = has("overview"), oracle = overview && has("oracle"), remote = oracle && has("remote_workspaces"), inbox = has("inbox"), backup = has("backup");
  const [source] = useState(() => new WidgetStreams());
  const analyticsRef = useRef(onAnalytics); analyticsRef.current = onAnalytics;
  const taskOpen = useRef(onOpenTerminal); taskOpen.current = onOpenTerminal;
  const [needsOpen, setNeedsOpen] = useState(false);
  // The Fleet monitor layout (owner-approved mock A, 2026-11-24): tabs over one page.
  const fleet = oracle && has("fleet_monitor");
  const [tab, setTab] = useState<"chat" | "fleet" | "analytics">("chat");
  const fieldRef = useRef<HTMLElement>(null);
  const registry = useMemo(() => [...oracleWidgets(tab => analyticsRef.current(tab), { oracle, remote, backup, onNeedsYou: () => setNeedsOpen(o => !o) }), ...(inbox ? [tasksWidget(key => taskOpen.current(key))] : [])], [oracle, remote, inbox, backup]);
  const [hover, setHover] = useState<{ key: string; el: HTMLElement } | null>(null);
  const [pinned, setPinned] = useState<{ key: string; el: HTMLElement } | null>(null);

  // Escape closes a pinned card first, then leaves the tower. Not while
  // typing, where Escape belongs to the text field.
  const pinnedRef = useRef(pinned);
  pinnedRef.current = pinned;
  const needsWindowRef = useRef(false);
  needsWindowRef.current = fleet && needsOpen;
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      if (needsWindowRef.current) return setNeedsOpen(false);
      if (pinnedRef.current) return setPinned(null);
      if (e.target instanceof Element && e.target.closest("textarea, input")) return;
      onBack();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onBack]);

  const active = pinned ?? hover;
  const activeAgent = active ? agents.find((a) => a.key === active.key) : undefined;
  const relay = useRelay(oracle);
  // Oldest first: the longest wait is the most urgent.
  const needs = (oracle ? relay.notes : [])
    .filter((n) => n.status === "open" && n.urgency !== "offer")
    .sort((a, b) => a.created_at - b.created_at);
  const teamList = teams(agents);
  useEffect(() => { source.emit("sessions", { status: "ready", value: agents }); }, [source, agents]);
  useEffect(() => { source.emit("needs-you", relay.loaded ? { status: "ready", value: { notes: relay.notes.filter(n => n.status === "open" && n.urgency !== "offer").sort((a, b) => a.created_at - b.created_at), now } } : { status: "loading" }); }, [source, relay.notes, relay.loaded, now]);

  if (!overview) return null;
  const teamsSection = (
    <>
      <h2 className="rd-teams-title">Your agents <small>{agents.length} across {teamList.length} {teamList.length === 1 ? "team" : "teams"}</small></h2>
      <section className="rd-tower-teams" aria-label="Teams">
        {teamList.length === 0 && <p className="rd-panel-empty">No agents yet.</p>}
        {teamList.map(team => <div key={team.name} className="rd-tower-team"><div className="rd-tower-team-head"><span className="rd-tower-team-name">{team.name}</span><small>{team.agents.length}</small></div><div className="rd-tower-field">{team.agents.map(a => <AgentDuck key={a.key} agent={a} stuck={looksStuck(a, now)} pinned={pinned?.key === a.key} onHover={el => setHover(el ? { key: a.key, el } : null)} onPin={el => setPinned({ key: a.key, el })} />)}</div></div>)}
      </section>
    </>
  );
  const needsCards = (
    <>
      {needs.length === 0 && <p className="rd-panel-empty">Nothing needs you.</p>}
      <div className="rd-tower-needs-list">
        {needs.map((n) => (
          <div key={n.id} className="rd-tower-need">
            <OracleDuck agent={agents.find((a) => a.key === n.session_key)} pose="waiting" size={28} />
            <NoteCard note={n} rules={relay.rules} onChange={relay.refresh} onOpenTerminal={onOpenTerminal} />
          </div>
        ))}
      </div>
    </>
  );
  const widgets = <Widgets surface="oracle" slot="oracle" registry={registry} source={source} kindLabel={oracle ? "Oracle" : "fleet"} />;
  // The Agent fleet tab: teams split into pods by subfolder, each duck saying
  // what it is doing, with arrows when one agent writes to another.
  const fleetTeams = (
    <>
      <h2 className="rd-teams-title">Your agents <small>{agents.length} across {teamList.length} {teamList.length === 1 ? "team" : "teams"}</small></h2>
      <section ref={fieldRef} className="rd-tower-teams rd-fleet-teams" aria-label="Teams">
        {teamList.length === 0 && <p className="rd-panel-empty">No agents yet.</p>}
        {teamList.map((team) => { const teamPods = pods(team.agents); return (
          <div key={team.name} className={`rd-tower-team${teamPods.length > 1 ? " rd-fleet-multi" : ""}`}>
            <div className="rd-tower-team-head"><span className="rd-tower-team-name">{team.name}</span><small>{team.agents.length}</small></div>
            <div className="rd-fleet-pods">
            {teamPods.map((pod) => (
              <div key={pod.name} className="rd-fleet-pod" role="group" aria-label={pod.name || team.name}>
                {pod.name && <div className="rd-fleet-pod-name">{pod.name}</div>}
                <div className="rd-tower-field">
                  {pod.agents.map((a) => <AgentDuck key={a.key} agent={a} stuck={looksStuck(a, now)} doing={doing(a, looksStuck(a, now))} pinned={pinned?.key === a.key} onHover={el => setHover(el ? { key: a.key, el } : null)} onPin={el => setPinned({ key: a.key, el })} />)}
                </div>
              </div>
            ))}
            </div>
          </div>
        ); })}
        <FleetArrows field={fieldRef} active={tab === "fleet"} />
      </section>
    </>
  );
  const factsSection = (
    <section className="rd-tower-facts" aria-label="Facts">
      {facts(agents, now).map((f) => (
        <span key={f.label} className="rd-tower-fact">
          {f.label}: <b>{f.value}</b>
        </span>
      ))}
    </section>
  );
  const agentCard = active && activeAgent && (
    <AgentCard
      key={`${activeAgent.key}-${pinned ? "pinned" : "hover"}`}
      agent={activeAgent}
      anchor={active.el}
      pinned={pinned !== null}
      stuck={looksStuck(activeAgent, now)}
      now={now}
      onClose={() => setPinned(null)}
      onOpenTerminal={() => onOpenTerminal(activeAgent.key)}
    />
  );

  if (fleet) {
    const TABS = [["chat", "Ask Oracle"], ["fleet", "Agent fleet"], ["analytics", "Analytics"]] as const;
    const panel = (id: typeof tab) => ({ id: `rd-fleet-panel-${id}`, role: "tabpanel", "aria-labelledby": `rd-fleet-tab-${id}`, hidden: tab !== id });
    const needAgents = new Set(needs.map((n) => n.session_key)).size;
    return (
      <div className="rd-tower rd-tower-widgets rd-fleet">
        <div className="rd-fleet-main">
        <div className="rd-tower-bar">
          <button className="rd-btn rd-btn-ghost rd-btn-sm" onClick={onBack} title="Back to sessions (Esc)">
            ← Sessions
          </button>
          <h1 className="rd-tower-title">
            Fleet monitor <small>Oracle · all workspaces</small>
          </h1>
        </div>
        <div className="rd-fleet-tabs" role="tablist" aria-label="Fleet monitor" onKeyDown={(e) => {
          const step = { ArrowRight: 1, ArrowLeft: -1 }[e.key];
          if (!step) return;
          const next = TABS[(TABS.findIndex(([id]) => id === tab) + step + TABS.length) % TABS.length][0];
          setTab(next);
          e.currentTarget.querySelector<HTMLElement>(`#rd-fleet-tab-${next}`)?.focus();
        }}>
          {TABS.map(([id, label]) => (
            <button key={id} id={`rd-fleet-tab-${id}`} role="tab" aria-selected={tab === id} aria-controls={`rd-fleet-panel-${id}`} tabIndex={tab === id ? 0 : -1} className={tab === id ? "on" : ""} onClick={() => setTab(id)}>{label}</button>
          ))}
        </div>
        {/* Every tab stays mounted, so an unsent draft or a pending Send card
            survives a look at another tab. */}
        <div className="rd-fleet-body">
          <div {...panel("chat")} className="rd-tower-oracle rd-fleet-chat">
              {needs.length > 0 && (
                <div className="rd-fleet-needsbar">
                  <span className="rd-fleet-needsbar-ducks" aria-hidden="true">
                    {needs.slice(0, 4).map((n) => <OracleDuck key={n.id} agent={agents.find((a) => a.key === n.session_key)} pose="waiting" size={22} />)}
                  </span>
                  <span className="rd-fleet-needsbar-text">
                    <b>{needs.length}</b> {needs.length === 1 ? "request" : "requests"} from {needAgents} {needAgents === 1 ? "agent" : "agents"}. Oldest: {needs[0].name}, {ago(now - needs[0].created_at)} ago
                  </span>
                  <button className="rd-btn rd-btn-sm rd-fleet-needsbar-open" onClick={() => setNeedsOpen(true)}>Open Needs you</button>
                </div>
              )}
              <OracleChat relay={relay} onRelayChange={relay.refresh} onOpenTerminal={onOpenTerminal} />
          </div>
          <div {...panel("fleet")} className="rd-tower-scroll">{fleetTeams}{factsSection}</div>
          <div {...panel("analytics")} className="rd-tower-scroll">
            {widgets}
            <button className="rd-btn rd-btn-ghost rd-btn-sm" onClick={() => onAnalytics("tokens")}>Full analytics ↗</button>
          </div>
        </div>
        </div>
        <FleetPulse needs={needs} now={now} onNeeds={() => setNeedsOpen(true)} onOpenTerminal={onOpenTerminal} />
        {needsOpen && (
          <>
            <div className="rd-fleet-scrim" onClick={() => setNeedsOpen(false)} />
            <NeedsWindow count={needs.length} onClose={() => setNeedsOpen(false)}>{needsCards}</NeedsWindow>
          </>
        )}
        {agentCard}
      </div>
    );
  }
  return (
    <div className={`rd-tower rd-tower-widgets${oracle ? "" : " rd-tower-passive"}`}>
      <div className="rd-tower-bar">
        <button className="rd-btn rd-btn-ghost rd-btn-sm" onClick={onBack} title="Back to sessions (Esc)">
          ← Sessions
        </button>
        <h1 className="rd-tower-title">
          Control tower <small>{oracle ? "Oracle · fleet monitor" : "Agent overview"}</small>
        </h1>
        <button className="rd-btn rd-btn-ghost rd-btn-sm rd-analytics-link" onClick={() => onAnalytics("tokens")}>Analytics ↗</button>
      </div>

      <div className="rd-tower-columns">
      {oracle && <aside className="rd-tower-oracle" aria-label="Ask Oracle">
        <OracleChat relay={relay} onRelayChange={relay.refresh} onOpenTerminal={onOpenTerminal} agents={agents.map(a => ({ id: a.key, name: a.label, folder: a.group ?? "" }))} />
      </aside>}
      <div className="rd-tower-scroll">
      {teamsSection}
      {oracle && needsOpen && (
        <section className="rd-tower-needs" aria-label="Needs you">
          <h2>
            Needs you <span>{needs.length} open, oldest first.</span>
            <button className="rd-btn rd-btn-ghost rd-btn-sm" onClick={() => setNeedsOpen(false)}>Close</button>
          </h2>
          {needsCards}
        </section>
      )}
      {widgets}
      {factsSection}
      </div>
      </div>

      {agentCard}
    </div>
  );
}

// Focus moves in on open, Tab stays inside, and closing returns focus to
// whatever opened it (the banner button or the Analytics tile). Escape is
// handled by the tower's key listener.
function NeedsWindow({ count, onClose, children }: { count: number; onClose: () => void; children: ReactNode }) {
  const box = useRef<HTMLElement>(null);
  useEffect(() => {
    const previous = document.activeElement;
    box.current?.focus();
    return () => { if (previous instanceof HTMLElement && previous.isConnected) previous.focus(); };
  }, []);
  return (
    <section ref={box} className="rd-fleet-needs-window" role="dialog" aria-label="Needs you" aria-modal="true" tabIndex={-1} onKeyDown={(e) => {
      if (e.key !== "Tab") return;
      const items = [...e.currentTarget.querySelectorAll<HTMLElement>("button:not(:disabled),input:not(:disabled),textarea:not(:disabled),select:not(:disabled),a[href]")];
      const first = items[0], last = items[items.length - 1];
      if (e.shiftKey && (document.activeElement === first || document.activeElement === e.currentTarget)) { e.preventDefault(); last?.focus(); }
      else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first?.focus(); }
    }}>
      <header>
        <h2>Needs you</h2>
        <span>{count} open, oldest first</span>
        <button className="rd-btn rd-btn-ghost rd-btn-sm" onClick={onClose}>Close</button>
      </header>
      <div className="rd-fleet-needs-cards">{children}</div>
    </section>
  );
}

function OracleDuck({ agent, pose, size }: { agent?: TowerAgent; pose: Parameters<typeof Duck>[0]["pose"]; size: number }) {
  return agent && sessionLocation(agent).remote
    ? <SessionLocationDuck session={agent} pose={pose} height={size} focusable={false} />
    : <Duck session={agent} pose={pose} size={size} />;
}

function AgentDuck({
  agent,
  stuck,
  doing,
  pinned,
  onHover,
  onPin,
}: {
  agent: TowerAgent;
  stuck: boolean;
  doing?: string;
  pinned: boolean;
  onHover: (el: HTMLElement | null) => void;
  onPin: (el: HTMLElement) => void;
}) {
  const state = agent.shownState;
  const pose = stuck ? "idle" : poseFor(state, !!agent.attentionSince);
  const subs = runningSubagents(agent);
  return (
    <button
      type="button"
      data-duck={agent.key}
      className={`rd-tower-duck s-${state}${stuck ? " stuck" : ""}${pinned ? " pinned" : ""}${pose === "sleeping" ? " resting" : ""}`}
      aria-label={`${agent.label}, ${teamOf(agent)}, ${stuck ? "looks stuck" : (state === "unknown" ? "Status unavailable" : STATE_WORD[state] ?? state)}${sessionLocation(agent).remote ? `, ${sessionLocation(agent).label}` : ""}`}
      onMouseEnter={(e) => onHover(e.currentTarget)}
      onMouseLeave={() => onHover(null)}
      onFocus={(e) => onHover(e.currentTarget)}
      onBlur={() => onHover(null)}
      onClick={(e) => onPin(e.currentTarget)}
    >
      <span
        className="rd-tower-duck-body"
      >
        <OracleDuck agent={agent} pose={pose} size={28} />
        {(agent.inboxPending ?? 0) > 0 && <span className="rd-tower-mail">✉ {agent.inboxPending}</span>}
        {subs > 0 && (
          <span className="rd-tower-ducklings" aria-hidden="true">
            {Array.from({ length: Math.min(subs, 3) }, (_, i) => <Duck key={i} pose="idle" size={12} />)}
            {subs > 3 && <em>+{subs - 3}</em>}
          </span>
        )}
      </span>
      <span className="rd-tower-duck-name">{agent.label}</span>
      {doing && <span className="rd-fleet-doing">{doing}</span>}
    </button>
  );
}

function AgentCard({
  agent,
  anchor,
  pinned,
  stuck,
  now,
  onClose,
  onOpenTerminal,
}: {
  agent: TowerAgent;
  anchor: HTMLElement;
  pinned: boolean;
  stuck: boolean;
  now: number;
  onClose: () => void;
  onOpenTerminal: () => void;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const [pos, setPos] = useState<{ left: number; top: number } | null>(null);
  const [mode, setMode] = useState<Mode>("inbox");
  const [text, setText] = useState("");
  const [sending, setSending] = useState(false);
  const [result, setResult] = useState<{ ok: boolean; text: string } | null>(null);
  const state = agent.shownState;
  const canType = state === "idle" && !stuck;

  useEffect(() => {
    const place = () => {
      const card = ref.current;
      if (!card) return;
      const r = anchor.getBoundingClientRect();
      let left = r.left + r.width / 2 - card.offsetWidth / 2;
      let top = r.bottom + 8;
      if (top + card.offsetHeight > window.innerHeight - 12) top = r.top - card.offsetHeight - 8;
      left = Math.max(12, Math.min(left, window.innerWidth - card.offsetWidth - 12));
      setPos({ left, top: Math.max(12, top) });
    };
    place();
    window.addEventListener("resize", place);
    window.addEventListener("scroll", place, true);
    return () => { window.removeEventListener("resize", place); window.removeEventListener("scroll", place, true); };
  }, [anchor, pinned, mode, result]);

  useEffect(() => {
    if (!pinned) return;
    const onDown = (e: MouseEvent) => {
      const t = e.target as Node;
      if (!ref.current?.contains(t) && !anchor.contains(t)) onClose();
    };
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, [pinned, anchor, onClose]);

  async function send() {
    if (!text.trim() || sending) return;
    setSending(true);
    setResult(null);
    try {
      await api.messageSession(agent.key, text.trim(), mode);
      setResult({ ok: true, text: mode === "inbox" ? `In ${agent.label}'s inbox.` : `Typed into ${agent.label}'s prompt.` });
      setText("");
    } catch (e) {
      setResult({ ok: false, text: (e as Error).message });
    } finally {
      setSending(false);
    }
  }

  const summary = agent.progress?.summary ? firstSentence(agent.progress.summary) : agent.intention;
  const hint =
    mode === "prompt"
      ? "Starts a new turn right away, as if you typed it."
      : state === "busy"
        ? "It sees this when its current turn ends."
        : state === "waiting"
          ? "It's waiting on you; answering in its terminal may be what it needs."
          : "Oracle wakes it to read this within a minute or two.";

  return (
    <div
      ref={ref}
      className="rd-tower-card"
      role={pinned ? "dialog" : "tooltip"}
      aria-label={`${agent.label} details`}
      style={pos ? { left: pos.left, top: pos.top } : { visibility: "hidden" }}
    >
      <div className="rd-tower-card-head">
        <span className="rd-tower-card-name">{agent.label}</span>
        <span className={`rd-tower-pill s-${stuck ? "stuck" : state}`}>{stuck ? "Looks stuck" : (state === "unknown" ? "Status unavailable" : STATE_WORD[state] ?? state)}</span>
      </div>
      <div className="rd-tower-card-meta">
        {[sessionLocation(agent).remote ? sessionLocation(agent).label : null, agent.group || "No workspace", agent.runtime, agent.model].filter(Boolean).join(" · ")} · updated {ago(now - agent.updatedAt)} ago
      </div>
      <div className="rd-tower-card-work">
        <span className="rd-tower-label">Working on</span>
        {summary || "No summary yet."}
      </div>
      <div className="rd-tower-card-row">
        {state === "busy" && agent.lastTool && <span>Running <b>{agent.lastTool}</b></span>}
        {agent.contextTokens ? <span>Context <b>{compact(agent.contextTokens)}</b></span> : null}
        {(agent.inboxPending ?? 0) > 0 && <span>Unread mail <b>{agent.inboxPending}</b></span>}
        {runningSubagents(agent) > 0 && <span>Sub-agents <b>{runningSubagents(agent)}</b></span>}
      </div>
      {!pinned && <div className="rd-tower-hint">Click to pin and message</div>}
      {pinned && (
        <div className="rd-tower-composer">
          <div className="rd-tower-seg" role="group" aria-label="How to deliver">
            <button type="button" aria-pressed={mode === "inbox"} onClick={() => setMode("inbox")}>
              Inbox message
            </button>
            <button
              type="button"
              aria-pressed={mode === "prompt"}
              disabled={!canType}
              title={canType ? undefined : "Only while the agent is idle"}
              onClick={() => setMode("prompt")}
            >
              Type into its prompt
            </button>
          </div>
          <textarea
            aria-label={`Message ${agent.label}`}
            placeholder={`Message ${agent.label}`}
            value={text}
            autoFocus
            onChange={(e) => setText(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) void send();
            }}
          />
          <span className="rd-tower-hint">{hint}</span>
          {result && (
            <span className={result.ok ? "rd-tower-ok" : "rd-tower-error"} role={result.ok ? "status" : "alert"}>
              {result.text}
            </span>
          )}
          <div className="rd-tower-card-actions">
            <button className="rd-btn rd-btn-primary rd-btn-sm" disabled={sending || !text.trim()} onClick={() => void send()}>
              {sending ? "Sending…" : "Send"}
            </button>
            <button className="rd-btn rd-btn-ghost rd-btn-sm" onClick={onOpenTerminal}>
              Open terminal
            </button>
            <span className="rd-spacer" />
            <button className="rd-btn rd-btn-ghost rd-btn-sm" onClick={onClose}>
              Close
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

function doing(a: TowerAgent, stuck: boolean): string {
  if (stuck) return "Looks stuck";
  if (a.shownState === "busy") return a.progress?.summary ? firstSentence(a.progress.summary) : a.lastTool ? `Using ${a.lastTool}` : "Working";
  return a.shownState === "unknown" ? "Status unavailable" : STATE_WORD[a.shownState] ?? a.shownState;
}

function firstSentence(text: string): string {
  const m = text.match(/^.*?[.!?](\s|$)/);
  return (m ? m[0] : text).trim();
}
