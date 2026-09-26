import { useEffect, useRef, useState } from "react";
import { api, TowerInsights } from "./api";
import { Duck, poseFor } from "./Duck";
import { OracleChat } from "./OracleChat";
import { useRelay } from "./relay";
import {
  TowerAgent,
  ago,
  compact,
  facts,
  looksStuck,
  placement,
  ROW_PX,
  runningSubagents,
  teamOf,
  teams,
} from "./tower";

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
}: {
  agents: TowerAgent[];
  now: number;
  onBack: () => void;
  onOpenTerminal: (key: string) => void;
}) {
  const [insights, setInsights] = useState<TowerInsights | null>(null);
  const [insightsError, setInsightsError] = useState("");
  const [hover, setHover] = useState<{ key: string; el: HTMLElement } | null>(null);
  const [pinned, setPinned] = useState<{ key: string; el: HTMLElement } | null>(null);

  useEffect(() => {
    let stopped = false;
    let timer: ReturnType<typeof setTimeout>;
    async function refresh() {
      try {
        const next = await api.controlTower();
        if (!stopped) { setInsights(next); setInsightsError(""); }
      } catch (e) {
        if (!stopped) setInsightsError((e as Error).message);
      } finally {
        if (!stopped) timer = setTimeout(refresh, 60_000);
      }
    }
    void refresh();
    return () => { stopped = true; clearTimeout(timer); };
  }, []);

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
  const count = (s: string) => agents.filter((a) => a.shownState === s).length;
  const live = count("busy") + count("waiting") + count("idle");
  const resting = agents.length - live;
  const teamList = teams(agents);

  return (
    <div className="rd-tower">
      <div className="rd-tower-scroll">
      <div className="rd-tower-bar">
        <button className="rd-btn rd-btn-ghost rd-btn-sm" onClick={onBack} title="Back to sessions (Esc)">
          ← Sessions
        </button>
        <h1 className="rd-tower-title">
          Control tower <small>Oracle · fleet monitor</small>
        </h1>
      </div>

      <section className="rd-tower-tiles" aria-label="Fleet insights">
        <Tile label="Agents" value={String(agents.length)}>
          <div className="rd-tower-stack" aria-hidden="true">
            {(["busy", "waiting", "idle"] as const).map((s) => (
              <span key={s} className={`rd-tower-stack-${s}`} style={{ width: `${(count(s) / Math.max(1, agents.length)) * 100}%` }} />
            ))}
          </div>
          {count("busy")} busy · {count("waiting")} waiting · {count("idle")} idle
          {resting > 0 && ` · ${resting} resting`}
          <br />
          across {teamList.length} team{teamList.length === 1 ? "" : "s"}
        </Tile>
        <Tile label="Needs you" value={String(needs.length)} warn={needs.length > 0}>
          {needs.length
            ? `Oldest: ${needs[0].name}${needs[0].folder ? ` (${needs[0].folder.split("/")[0]})` : ""}, ${ago(now - needs[0].created_at)} ago`
            : "Nothing needs you"}
        </Tile>
        <TokensTile insights={insights} />
        <Tile label="Agent mail · 24h" value={insights ? String(insights.mail.sent) : "…"}>
          {insights
            ? `${insights.mail.answered} answered · ${insights.mail.nudges} Oracle nudge${insights.mail.nudges === 1 ? "" : "s"}`
            : "Loading"}
        </Tile>
        <BackupTile insights={insights} now={now} />
        <Tile label="Remote sessions" value={insights?.remote.available ? String(insights.remote.count) : "0"}>
          {insights?.remote.available ? "Running on the remote workspace" : "Remote workspace isn't set up yet"}
        </Tile>
      </section>
      {insightsError && <p className="rd-tower-error" role="alert">Couldn't load insights: {insightsError}</p>}

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
            Needs you <span>{needs.length} open, oldest first. Answer in the chat.</span>
          </h2>
          <div className="rd-tower-needs-list">
            {needs.map((n) => (
              <button key={n.id} className="rd-tower-need" onClick={() => showNote(n.id)}>
                <Duck pose="waiting" size={28} />
                <span className="rd-tower-need-who">
                  <b>{n.name}</b>{n.folder ? ` · ${n.folder.split("/")[0]}` : ""}
                  <small>{n.kind === "approval" ? `Wants to run ${n.detail || n.tool}` : n.question}</small>
                </span>
                <span className="rd-tower-need-age">{ago(now - n.created_at)}</span>
              </button>
            ))}
          </div>
        </section>
      )}
      <section className="rd-tower-teams" aria-label="Teams">
          {teamList.length === 0 && <p className="rd-panel-empty">No agents yet.</p>}
          {teamList.map((team) => {
            const { rows, spots } = placement(team.agents.map((a) => a.key));
            const parts = (["busy", "waiting", "idle"] as const)
              .map((s) => [s, team.agents.filter((a) => a.shownState === s).length] as const)
              .filter(([, n]) => n > 0)
              .map(([s, n]) => `${n} ${s}`);
            return (
              <div key={team.name} className={`rd-tower-team${team.agents.length > 4 ? " wide" : ""}`}>
                <div className="rd-tower-team-head">
                  <span className="rd-tower-team-name">{team.name}</span>
                  <span className="rd-tower-team-meta">{parts.join(" · ")}</span>
                </div>
                <div className="rd-tower-field" style={{ height: rows * ROW_PX + 24 }}>
                  {team.agents.map((a, i) => (
                    <AgentDuck
                      key={a.key}
                      agent={a}
                      spot={spots[i]}
                      stuck={looksStuck(a, now)}
                      pinned={pinned?.key === a.key}
                      onHover={(el) => setHover(el ? { key: a.key, el } : null)}
                      onPin={(el) => setPinned({ key: a.key, el })}
                    />
                  ))}
                </div>
              </div>
            );
          })}
        </section>
      </div>

      <aside className="rd-tower-oracle" aria-label="Ask Oracle">
        <OracleChat relay={relay} onRelayChange={relay.refresh} />
      </aside>

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

function Tile({ label, value, warn, children }: { label: string; value: string; warn?: boolean; children: React.ReactNode }) {
  return (
    <div className={`rd-tower-tile${warn ? " warn" : ""}`}>
      <span className="rd-tower-label">{label}</span>
      <span className="rd-tower-num">{value}</span>
      <span className="rd-tower-sub">{children}</span>
    </div>
  );
}

function TokensTile({ insights }: { insights: TowerInsights | null }) {
  if (!insights) return <Tile label="Tokens · 7 days" value="…">Counting transcripts</Tile>;
  const agents = Object.entries(insights.tokens.by_agent);
  const sum = (t: { input: number; cache_read: number; cache_write: number; output: number }) =>
    t.input + t.cache_read + t.cache_write + t.output;
  const total = agents.reduce((n, [, t]) => n + sum(t), 0);
  const inputs = agents.reduce((n, [, t]) => n + t.input + t.cache_read + t.cache_write, 0);
  const cached = agents.reduce((n, [, t]) => n + t.cache_read, 0);
  const written = agents.reduce((n, [, t]) => n + t.output, 0);
  const names: Record<string, string> = { "claude-code": "Claude Code", codex: "Codex" };
  return (
    <Tile label={`Tokens · ${insights.tokens.days} days`} value={compact(total)}>
      <span title="All Claude Code and Codex transcripts on this Mac">
        {inputs ? `${Math.round((cached / inputs) * 100)}% read from cache · ` : ""}
        {compact(written)} written
        <br />
        {agents.map(([k, t]) => `${names[k] ?? k} ${compact(sum(t))}`).join(" · ")}
      </span>
    </Tile>
  );
}

function BackupTile({ insights, now }: { insights: TowerInsights | null; now: number }) {
  if (!insights) return <Tile label="Last backup" value="…">Loading</Tile>;
  const b = insights.backup;
  const where = b.destination === "gcs" ? "Google Cloud Storage" : b.destination === "local" ? "a local folder" : null;
  if (b.status === "succeeded" && b.finished_at) {
    const old = now - b.finished_at > 7 * 86_400_000;
    return (
      <Tile label="Last backup" value={`${ago(now - b.finished_at)} ago`} warn={old}>
        {where ? `To ${where}` : "Completed"}
      </Tile>
    );
  }
  if (b.status === "running") return <Tile label="Last backup" value="Running">{where ? `To ${where}` : ""}</Tile>;
  return (
    <Tile label="Last backup" value="None" warn>
      {where ? `Destination set (${where}), no completed backup recorded` : "No backup destination set"}
      {b.status === "failed" || b.status === "interrupted" ? `; the last attempt ${b.status === "failed" ? "failed" : "was interrupted"}` : ""}
    </Tile>
  );
}

function AgentDuck({
  agent,
  spot,
  stuck,
  pinned,
  onHover,
  onPin,
}: {
  agent: TowerAgent;
  spot: { left: number; top: number; drift: number };
  stuck: boolean;
  pinned: boolean;
  onHover: (el: HTMLElement | null) => void;
  onPin: (el: HTMLElement) => void;
}) {
  const state = agent.shownState;
  const pose = stuck ? "idle" : poseFor(state);
  const subs = runningSubagents(agent);
  const drifts = pose === "idle" && !stuck;
  return (
    <button
      type="button"
      data-duck={agent.key}
      className={`rd-tower-duck s-${state}${stuck ? " stuck" : ""}${pinned ? " pinned" : ""}${pose === "sleeping" ? " resting" : ""}`}
      style={{ left: `${spot.left}%`, top: spot.top }}
      aria-label={`${agent.label}, ${teamOf(agent)}, ${stuck ? "looks stuck" : STATE_WORD[state] ?? state}`}
      onMouseEnter={(e) => onHover(e.currentTarget)}
      onMouseLeave={() => onHover(null)}
      onFocus={(e) => onHover(e.currentTarget)}
      onBlur={() => onHover(null)}
      onClick={(e) => onPin(e.currentTarget)}
    >
      <span
        className={`rd-tower-duck-body${drifts ? " drift" : ""}`}
        style={{ "--dx": `${spot.drift}px`, "--dur": `${12 + Math.abs(spot.drift) / 3}s`, "--delay": `-${Math.abs(spot.drift) / 4}s` } as React.CSSProperties}
      >
        <Duck pose={pose} size={52} />
        {(agent.inboxPending ?? 0) > 0 && <span className="rd-tower-mail">✉ {agent.inboxPending}</span>}
        {subs > 0 && (
          <span className="rd-tower-ducklings" aria-hidden="true">
            {Array.from({ length: Math.min(subs, 3) }, (_, i) => <Duck key={i} pose="idle" size={20} />)}
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
        {[agent.group || "No folder", agent.runtime, agent.model].filter(Boolean).join(" · ")} · updated {ago(now - agent.updatedAt)} ago
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
