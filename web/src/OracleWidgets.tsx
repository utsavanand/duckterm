import { AnalyticsTab } from "./analyticsData";
import { RelayNote, TowerInsights } from "./api";
import { TowerAgent, ago, compact, teams } from "./tower";
import { WidgetType } from "./Widgets";
import { StreamState } from "./widgetStreams";
function value<T>(state: StreamState): T { return state.status === "ready" ? state.value as T : undefined as T; }
export function oracleWidgets(onAnalytics: (tab: AnalyticsTab) => void): WidgetType[] {
  return [
    { type: "agents", title: "Agents", slots: ["oracle"], streams: ["sessions"], render: ({ streams }) => {
      const agents = value<TowerAgent[]>(streams.sessions), count = (state: string) => agents.filter(a => a.shownState === state).length;
      const resting = agents.length - count("busy") - count("waiting") - count("idle");
      return <Tile label="Agents" value={String(agents.length)}><div className="rd-tower-stack" aria-hidden="true">{["busy", "waiting", "idle"].map(state => <span key={state} className={`rd-tower-stack-${state}`} style={{ width: `${count(state) / Math.max(1, agents.length) * 100}%` }} />)}</div>{count("busy")} busy · {count("waiting")} waiting · {count("idle")} idle{resting > 0 && ` · ${resting} resting`}<br />across {teams(agents).length} {teams(agents).length === 1 ? "team" : "teams"}</Tile>;
    } },
    { type: "needs-you", title: "Needs you", slots: ["oracle"], streams: ["needs-you"], render: ({ streams }) => {
      const { notes, now } = value<{ notes: RelayNote[]; now: number }>(streams["needs-you"]);
      return <Tile label="Needs you" value={String(notes.length)} warn={notes.length > 0}>{notes.length ? `Oldest: ${notes[0].name}${notes[0].folder ? ` (${notes[0].folder.split("/")[0]})` : ""}, ${ago(now - notes[0].created_at)} ago` : "Nothing needs you"}</Tile>;
    } },
    { type: "tokens", title: "Tokens · 7 days", slots: ["oracle"], streams: ["tokens"], render: ({ streams }) => <TokensTile tokens={value(streams.tokens)} onClick={() => onAnalytics("tokens")} /> },
    { type: "mail", title: "Agent mail · 24h", slots: ["oracle"], streams: ["mail"], render: ({ streams }) => {
      const mail = value<TowerInsights["mail"]>(streams.mail);
      return <Tile label="Agent mail · 24h" value={String(mail.sent)} onClick={() => onAnalytics("mail")}>{mail.answered} answered · {mail.nudges} Oracle {mail.nudges === 1 ? "nudge" : "nudges"}</Tile>;
    } },
    { type: "backup", title: "Last backup", slots: ["oracle"], streams: ["backup"], render: ({ streams }) => <BackupTile b={value(streams.backup)} now={Date.now()} /> },
    { type: "remote", title: "Remote sessions", slots: ["oracle"], streams: ["remote"], render: ({ streams }) => <Tile label="Remote sessions" value={String(value<TowerInsights["remote"]>(streams.remote).count)}>Running on the remote workspace</Tile> },
  ];
}
export function Tile({ label, value, warn, children, onClick }: { label: string; value: string; warn?: boolean; children: React.ReactNode; onClick?: () => void }) {
  const Tag = onClick ? "button" : "div";
  return (
    <Tag onClick={onClick} aria-label={onClick ? `Open ${label.startsWith("Tokens") ? "token" : "mail"} analytics` : undefined} className={`rd-tower-tile${warn ? " warn" : ""}${onClick ? " rd-tower-tile-button" : ""}`}>
      <span className="rd-tower-label">{label}</span>
      <span className="rd-tower-num">{value}</span>
      <span className="rd-tower-sub">{children}</span>
    </Tag>
  );
}

function TokensTile({ tokens, onClick }: { tokens: TowerInsights["tokens"]; onClick: () => void }) {
  const agents = Object.entries(tokens.by_agent);
  const sum = (t: { input: number; cache_read: number; cache_write: number; output: number }) =>
    t.input + t.cache_read + t.cache_write + t.output;
  const total = agents.reduce((n, [, t]) => n + sum(t), 0);
  const inputs = agents.reduce((n, [, t]) => n + t.input + t.cache_read + t.cache_write, 0);
  const cached = agents.reduce((n, [, t]) => n + t.cache_read, 0);
  const written = agents.reduce((n, [, t]) => n + t.output, 0);
  const names: Record<string, string> = { "claude-code": "Claude Code", codex: "Codex" };
  return (
    <Tile onClick={onClick} label={`Tokens · ${tokens.days} days`} value={compact(total)}>
      <span title="All Claude Code and Codex transcripts on this Mac">
        {inputs ? `${Math.round((cached / inputs) * 100)}% read from cache · ` : ""}
        {compact(written)} written
        <br />
        {agents.map(([k, t]) => `${names[k] ?? k} ${compact(sum(t))}`).join(" · ")}
      </span>
    </Tile>
  );
}

function BackupTile({ b, now }: { b: TowerInsights["backup"]; now: number }) {
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

