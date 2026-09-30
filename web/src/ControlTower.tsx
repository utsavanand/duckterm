import { useEffect, useRef, useState } from "react";
import { AnalyticsTab } from "./analyticsData";
import { api } from "./api";
import { Duck, poseFor } from "./Duck";
import { SessionLocationDuck, sessionLocation } from "./SessionLocationDuck";
import { OracleChat } from "./OracleChat";
import { useRelay } from "./relay";
import {
  TowerAgent,
  ago,
  compact,
  facts,
  looksStuck,
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

// Oracle's control tower: fleet insights, every agent drawn as the session
// mascot grouped by team (top-level folder), and a way to message one agent.
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
  const [source] = useState(() => new WidgetStreams());
  const analyticsRef = useRef(onAnalytics); analyticsRef.current = onAnalytics;
  const [registry] = useState(() => oracleWidgets(tab => analyticsRef.current(tab)));
  const [hover, setHover] = useState<{ key: string; el: HTMLElement } | null>(null);
  const [pinned, setPinned] = useState<{ key: string; el: HTMLElement } | null>(null);

  // Escape closes a pinned card first, then leaves the tower. Not while
  // typing, where Escape belongs to the text field.
  const pinnedRef = useRef(pinned);
  pinnedRef.current = pinned;
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      if (pinnedRef.current) return setPinned(null);
      if (e.target instanceof Element && e.target.closest("textarea, input")) return;
      onBack();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onBack]);

  const active = pinned ?? hover;
  const activeAgent = active ? agents.find((a) => a.key === active.key) : undefined;
  const relay = useRelay();
  // Oldest first: the longest wait is the most urgent.
  const needs = relay.notes
    .filter((n) => n.status === "open" && n.urgency !== "offer")
    .sort((a, b) => a.created_at - b.created_at);
  const teamList = teams(agents);
  useEffect(() => { source.emit("sessions", { status: "ready", value: agents }); }, [source, agents]);
  useEffect(() => { source.emit("needs-you", relay.loaded ? { status: "ready", value: { notes: relay.notes.filter(n => n.status === "open" && n.urgency !== "offer").sort((a, b) => a.created_at - b.created_at), now } } : { status: "loading" }); }, [source, relay.notes, relay.loaded, now]);

  return (
    <div className="rd-tower rd-tower-widgets">
      <div className="rd-tower-bar">
        <button className="rd-btn rd-btn-ghost rd-btn-sm" onClick={onBack} title="Back to sessions (Esc)">
          ← Sessions
        </button>
        <h1 className="rd-tower-title">
          Control tower <small>Oracle · fleet monitor</small>
        </h1>
        <button className="rd-btn rd-btn-ghost rd-btn-sm rd-analytics-link" onClick={() => onAnalytics("tokens")}>Analytics ↗</button>
      </div>

      <div className="rd-tower-columns">
      <aside className="rd-tower-oracle" aria-label="Ask Oracle">
        <OracleChat relay={relay} onRelayChange={relay.refresh} onOpenTerminal={onOpenTerminal} />
      </aside>
      <div className="rd-tower-scroll">
      <h2 className="rd-teams-title">Your agents <small>{agents.length} across {teamList.length} {teamList.length === 1 ? "team" : "teams"}</small></h2>
      <section className="rd-tower-teams" aria-label="Teams">
        {teamList.length === 0 && <p className="rd-panel-empty">No agents yet.</p>}
        {teamList.map(team => <div key={team.name} className="rd-tower-team"><div className="rd-tower-team-head"><span className="rd-tower-team-name">{team.name}</span><small>{team.agents.length}</small></div><div className="rd-tower-field">{team.agents.map(a => <AgentDuck key={a.key} agent={a} stuck={looksStuck(a, now)} pinned={pinned?.key === a.key} onHover={el => setHover(el ? { key: a.key, el } : null)} onPin={el => setPinned({ key: a.key, el })} />)}</div></div>)}
      </section>
      <Widgets surface="oracle" slot="oracle" registry={registry} source={source} />

      <section className="rd-tower-facts" aria-label="Facts">
        {facts(agents, now).map((f) => (
          <span key={f.label} className="rd-tower-fact">
            {f.label}: <b>{f.value}</b>
          </span>
        ))}
      </section>

      {needs.length > 0 && (
        <section className="rd-tower-needs" aria-label="Needs you">
          <h2>
            Needs you <span>{needs.length} open, oldest first.</span>
          </h2>
          <div className="rd-tower-needs-list">
            {needs.map((n) => (
              <button
                key={n.id}
                className="rd-tower-need"
                onClick={() => (n.kind === "choice" ? onOpenTerminal(n.session_key) : showNote(n.id))}
              >
                <OracleDuck agent={agents.find((a) => a.key === n.session_key)} pose="waiting" size={28} />
                <span className="rd-tower-need-who">
                  <b>{n.name}</b>{n.folder ? ` · ${n.folder.split("/")[0]}` : ""}
                  <small>
                    {n.kind === "approval"
                      ? `Wants to run ${n.detail || n.tool}`
                      : n.kind === "choice"
                        ? `${n.questions?.[0]?.question ?? n.question ?? "A menu question"} Answer in its terminal.`
                        : n.question}
                  </small>
                </span>
                <span className="rd-tower-urgency">{n.urgency === "offer" ? "Offer" : n.urgency === "approval" ? "Approval" : "Blocked"}</span>
                <span className="rd-tower-need-age">{ago(now - n.created_at)}</span>
              </button>
            ))}
          </div>
        </section>
      )}
      </div>
      </div>

      {active && activeAgent && (
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
      )}
    </div>
  );
}

function OracleDuck({ agent, pose, size }: { agent?: TowerAgent; pose: Parameters<typeof Duck>[0]["pose"]; size: number }) {
  return agent && sessionLocation(agent).remote
    ? <SessionLocationDuck session={agent} pose={pose} height={size} focusable={false} />
    : <Duck pose={pose} size={size} />;
}

function AgentDuck({
  agent,
  stuck,
  pinned,
  onHover,
  onPin,
}: {
  agent: TowerAgent;
  stuck: boolean;
  pinned: boolean;
  onHover: (el: HTMLElement | null) => void;
  onPin: (el: HTMLElement) => void;
}) {
  const state = agent.shownState;
  const pose = stuck ? "idle" : poseFor(state);
  const subs = runningSubagents(agent);
  return (
    <button
      type="button"
      data-duck={agent.key}
      className={`rd-tower-duck s-${state}${stuck ? " stuck" : ""}${pinned ? " pinned" : ""}${pose === "sleeping" ? " resting" : ""}`}
      aria-label={`${agent.label}, ${teamOf(agent)}, ${stuck ? "looks stuck" : STATE_WORD[state] ?? state}${sessionLocation(agent).remote ? `, ${sessionLocation(agent).label}` : ""}`}
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
        <span className={`rd-tower-pill s-${stuck ? "stuck" : state}`}>{stuck ? "Looks stuck" : STATE_WORD[state] ?? state}</span>
      </div>
      <div className="rd-tower-card-meta">
        {[sessionLocation(agent).remote ? sessionLocation(agent).label : null, agent.group || "No folder", agent.runtime, agent.model].filter(Boolean).join(" · ")} · updated {ago(now - agent.updatedAt)} ago
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

// Scroll the chat's own list to a note. scrollIntoView would also scroll the
// tower around it (the bug that hid the header).
function showNote(id: string) {
  const note = document.querySelector<HTMLElement>(`[data-note="${CSS.escape(id)}"]`);
  const log = note?.closest<HTMLElement>(".rd-oracle-log");
  if (!note || !log) return;
  log.scrollTop = note.offsetTop - log.offsetTop - 16;
  note.classList.add("flash");
  setTimeout(() => note.classList.remove("flash"), 1200);
  note.querySelector<HTMLElement>("textarea, button")?.focus({ preventScroll: true });
}

function firstSentence(text: string): string {
  const m = text.match(/^.*?[.!?](\s|$)/);
  return (m ? m[0] : text).trim();
}
