import { ReactNode, useEffect, useId, useRef, useState } from "react";
import {
  AnalyticsTab,
  Range,
  TokenAnalytics,
  MailAnalytics,
  SeriesRow,
  loadAnalytics,
  groupTokens,
  tokenTotal,
  sumValues,
  fillDays,
  windowStart,
  mailMedian,
} from "./analyticsData";
import { SessionView } from "./types";
import { splitSessionRef } from "./hostTransport";
import "./analytics.css";

const integer = (n: number) => new Intl.NumberFormat().format(Math.round(n));
const compact = (n: number) =>
  new Intl.NumberFormat(undefined, {
    notation: "compact",
    maximumFractionDigits: 2,
  }).format(n);
const dayLabel = (d: string) =>
  new Date(d + "T00:00Z").toLocaleDateString(undefined, {
    month: "short",
    day: "numeric",
    timeZone: "UTC",
  });
const duration = (ms: number | null) =>
  ms === null
    ? "—"
    : ms < 60000
      ? `${Math.round(ms / 1000)} sec`
      : ms < 3600000
        ? `${Math.round(ms / 60000)} min`
        : `${(ms / 3600000).toFixed(1)} hr`;
const colors = [
  "var(--an-blue)",
  "var(--an-green)",
  "var(--an-purple)",
  "var(--an-amber)",
  "var(--text-soft)",
];
const agents: Record<string, string> = {
  "claude-code": "Claude Code",
  codex: "Codex",
};

function Table({ headers, rows }: { headers: string[]; rows: ReactNode[][] }) {
  return (
    <div className="an-table" tabIndex={0} aria-label="Scrollable data table">
      <table>
        <thead>
          <tr>
            {headers.map((h) => (
              <th key={h} scope="col">
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, i) => (
            <tr key={i}>
              {row.map((value, j) => (
                <td key={j}>{value}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
function Chart({
  rows,
  labels,
  stacked = false,
  percent = false,
}: {
  rows: SeriesRow[];
  labels: string[];
  stacked?: boolean;
  percent?: boolean;
}) {
  const max = percent
    ? 100
    : Math.max(
        1,
        ...rows.map((r) =>
          stacked ? sumValues(r) : Math.max(...r.values.map((v) => v ?? 0)),
        ),
      );
  const host = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(520);
  useEffect(() => {
    const observer = new ResizeObserver(() => {
      if (host.current) setWidth(Math.max(520, host.current.clientWidth));
    });
    if (host.current) observer.observe(host.current);
    return () => observer.disconnect();
  }, []);
  const height = 240,
    left = 56,
    base = 204,
    slot = (width - left - 12) / Math.max(rows.length, 1),
    bar = Math.min(55, slot * 0.7);
  const titleId = useId();
  return (
    <>
      <div className="an-legend">
        {labels.map((l, i) => (
          <span key={l}>
            <i style={{ background: colors[i % colors.length] }} />
            {l}
          </span>
        ))}
      </div>
      <div
        className="an-chart-scroll"
        ref={host}
        tabIndex={0}
        aria-label="Scrollable chart; use Table for exact values"
      >
        <svg
          viewBox={`0 0 ${width} ${height}`}
          style={{ minWidth: 520, height: 240 }}
          role="img"
          aria-labelledby={titleId}
        >
          <title id={titleId}>
            {labels.join(", ")} by UTC day. Exact values in Table view.
          </title>
          {[0, 1, 2, 3, 4].map((i) => (
            <g key={i}>
              <line
                x1={left}
                x2={width - 12}
                y1={12 + i * 48}
                y2={12 + i * 48}
              />
              <text x={left - 9} y={16 + i * 48} textAnchor="end">
                {compact(max * (1 - i / 4))}
                {percent ? "%" : ""}
              </text>
            </g>
          ))}
          {rows.map((r, i) => {
            let y = base;
            return (
              <g key={r.label}>
                {r.values.map((value, j) => {
                  if (value === null) return null;
                  const h = (value / max) * 192;
                  y = stacked ? y - h : base - h;
                  return (
                    <rect
                      key={j}
                      x={
                        left +
                        i * slot +
                        (slot - bar) / 2 +
                        (stacked ? 0 : (j * bar) / r.values.length)
                      }
                      y={y}
                      width={stacked ? bar : (bar / r.values.length) * 0.9}
                      height={h}
                      fill={colors[j % colors.length]}
                    >
                      <title>
                        {r.label} · {labels[j]}: {integer(value)}
                        {percent ? "%" : ""}
                      </title>
                    </rect>
                  );
                })}
                {(i === 0 ||
                  i === rows.length - 1 ||
                  i % Math.max(1, Math.ceil(rows.length / 8)) === 0) && (
                  <text x={left + (i + 0.5) * slot} y={229} textAnchor="middle">
                    {dayLabel(r.label)}
                  </text>
                )}
              </g>
            );
          })}
        </svg>
      </div>
    </>
  );
}
function Panel({
  title,
  description,
  children,
  table,
  wide = false,
}: {
  title: string;
  description: string;
  children: ReactNode;
  table: ReactNode;
  wide?: boolean;
}) {
  const [view, setView] = useState<"chart" | "table">("chart");
  return (
    <section className={`an-panel${wide ? " an-wide" : ""}`}>
      <header>
        <div>
          <h2>{title}</h2>
          <p>{description}</p>
        </div>
        <div className="an-views" aria-label={`${title} view`}>
          <button
            aria-pressed={view === "chart"}
            onClick={() => setView("chart")}
          >
            Chart
          </button>
          <button
            aria-pressed={view === "table"}
            onClick={() => setView("table")}
          >
            Table
          </button>
        </div>
      </header>
      {view === "chart" ? children : table}
    </section>
  );
}
function Stat({
  label,
  value,
  detail,
}: {
  label: string;
  value: string;
  detail: string;
}) {
  return (
    <div className="an-stat">
      <span>{label}</span>
      <strong>{value}</strong>
      <small>{detail}</small>
    </div>
  );
}
function Ranking({
  rows,
  name,
}: {
  rows: SeriesRow[];
  name: (id: string) => string;
}) {
  const max = Math.max(1, ...rows.map(sumValues));
  return rows.length ? (
    <>
      {rows.map((r) => (
        <div className="an-rank" key={r.label}>
          <span title={name(r.label)}>{name(r.label)}</span>
          <strong>{compact(sumValues(r))}</strong>
          <div>
            <i style={{ width: `${(sumValues(r) / max) * 100}%` }} />
          </div>
        </div>
      ))}
    </>
  ) : (
    <p>No activity in this range.</p>
  );
}
function Empty() {
  return (
    <div className="an-empty">
      <h2>No activity in this view</h2>
      <p>
        Try a longer range or clear the filters. Unavailable history is not
        counted as zero activity.
      </p>
    </div>
  );
}
function Tokens({ data, range }: { data: TokenAnalytics; range: Range }) {
  const total = data.rows.reduce((n, r) => n + tokenTotal(r), 0),
    output = data.rows.reduce((n, r) => n + r.output, 0);
  const daily = fillDays(
    groupTokens(data.rows, "day"),
    windowStart(range, data.today, data.earliest_day ?? data.today),
    data.today,
    4,
  );
  const peak = daily.reduce<SeriesRow | null>(
    (a, b) => (!a || sumValues(a) < sumValues(b) ? b : a),
    null,
  );
  const rates = daily.map((r) => {
    const input = (r.values[0] ?? 0) + (r.values[1] ?? 0) + (r.values[2] ?? 0);
    return {
      label: r.label,
      values: [input ? (100 * (r.values[1] ?? 0)) / input : null],
    };
  });
  const names = (
    field: "model" | "agent" | "session" | "folder",
    key: string,
  ) =>
    field === "agent"
      ? (agents[key] ?? key)
      : field === "session"
        ? (data.options.sessions.find((v) => v.value === key)?.label ?? key)
        : field === "folder"
          ? (data.options.folders.find((v) => v.value === key)?.label ?? key)
          : key;
  return (
    <>
      <div className="an-stats">
        <Stat
          label="Total tokens"
          value={compact(total)}
          detail="Input, cache read, cache write and output"
        />
        <Stat
          label="Daily average"
          value={compact(total / Math.max(1, daily.length))}
          detail={`${daily.length} UTC calendar days in view`}
        />
        <Stat
          label="Busiest day"
          value={peak && total ? dayLabel(peak.label) : "—"}
          detail={
            peak && total
              ? `${compact(sumValues(peak))} tokens`
              : "No usage in this range"
          }
        />
        <Stat
          label="Output tokens"
          value={compact(output)}
          detail="Tokens generated by agents"
        />
      </div>
      {!total ? (
        <Empty />
      ) : (
        <div className="an-grid">
          <Panel
            wide
            title="Tokens over time"
            description="Daily usage · UTC calendar days"
            table={
              <Table
                headers={[
                  "Day (UTC)",
                  "Input",
                  "Cache read",
                  "Cache write",
                  "Output",
                  "Total",
                ]}
                rows={daily.map((r) => [
                  r.label,
                  ...r.values.map((v) => integer(v ?? 0)),
                  integer(sumValues(r)),
                ])}
              />
            }
          >
            <Chart
              rows={daily}
              labels={["Input", "Cache read", "Cache write", "Output"]}
              stacked
            />
          </Panel>
          {(["model", "agent", "session", "folder"] as const).map((field) => {
            const rows = groupTokens(data.rows, field);
            return (
              <Panel
                key={field}
                title={
                  {
                    model: "By model",
                    agent: "By agent",
                    session: "Top sessions",
                    folder: "Top folders",
                  }[field]
                }
                description={
                  field === "model"
                    ? "Exact model identifiers reported in transcripts"
                    : field === "session" || field === "folder"
                      ? "Current session names and folder placement"
                      : "Total tokens in the selected range"
                }
                table={
                  <Table
                    headers={[
                      field[0].toUpperCase() + field.slice(1),
                      "Tokens",
                    ]}
                    rows={rows.map((r) => [
                      names(field, r.label),
                      integer(sumValues(r)),
                    ])}
                  />
                }
              >
                <Ranking
                  rows={rows.slice(0, 20)}
                  name={(key) => names(field, key)}
                />
                {rows.length > 20 && (
                  <p>Top 20 shown. Table contains all {rows.length}.</p>
                )}
              </Panel>
            );
          })}
          <Panel
            wide
            title="Cache read rate"
            description="Cache read ÷ (input + cache read + cache write); gaps mean no input"
            table={
              <Table
                headers={["Day (UTC)", "Cache read rate"]}
                rows={rates.map((r) => [
                  r.label,
                  r.values[0] === null ? "—" : `${r.values[0].toFixed(1)}%`,
                ])}
              />
            }
          >
            <Chart rows={rates} labels={["Percent of input"]} percent />
          </Panel>
        </div>
      )}
      <p className="an-history">
        <strong>
          Available local history: {data.earliest_day ?? "none yet"}.
        </strong>{" "}
        Claude Code and Codex transcripts on this Mac only. Unmapped or
        ambiguous transcripts remain “Outside DuckTerm.” Missing model IDs
        appear as “Model not reported.” Remote transcripts are excluded.
      </p>
    </>
  );
}
function Mail({
  data,
  range,
  sessions,
}: {
  data: MailAnalytics;
  range: Range;
  sessions: SessionView[];
}) {
  const name = (id: string) =>
    sessions.find(
      (s) => s.key === id && splitSessionRef(s.key).host === "local",
    )?.label ?? (id ? `Session ${id}` : "Owner");
  const today = new Date().toISOString().slice(0, 10),
    enabled = new Date(data.enabled_at).toISOString().slice(0, 10);
  const start = windowStart(
    range,
    today,
    [enabled, ...data.daily.map((d) => d.day)].sort()[0],
  );
  const metrics = [
    "sent",
    "answered",
    "declined",
    "expired",
    "cancelled",
  ] as const;
  const daily = fillDays(
    data.daily.map((d) => ({ label: d.day, values: metrics.map((k) => d[k]) })),
    start,
    today,
    5,
  );
  const times = fillDays(
    data.daily.map((d) => ({
      label: d.day,
      values: [
        d.median_approx_ms === null ? null : d.median_approx_ms / 60000,
        d.slowest_ms === null ? null : d.slowest_ms / 60000,
      ],
    })),
    start,
    today,
    2,
    null,
  );
  const prompts = fillDays(
    data.daily.map((d) => ({ label: d.day, values: [d.broadcasts, d.nudges] })),
    start,
    today,
    2,
  );
  const median = mailMedian(data),
    sent = data.daily.reduce((n, d) => n + d.sent, 0),
    answered = data.daily.reduce((n, d) => n + d.answered, 0);
  const pairs = data.top_pairs.map((r, i) => ({
    label: String(i),
    values: [r.sent],
  }));
  return (
    <>
      <p className="an-history">
        <strong>Permanent mail history since {enabled}.</strong> Retained
        earlier messages may contribute; previously deleted messages cannot be
        recovered. Percentiles are approximate; counts and maximum times are
        exact.
      </p>
      <div className="an-stats">
        <Stat
          label="Questions sent"
          value={integer(sent)}
          detail="Sent during the selected range"
        />
        <Stat
          label="Questions answered"
          value={integer(answered)}
          detail="Completed during the selected range"
        />
        <Stat
          label="Typical answer time"
          value={median === null ? "—" : `≈ ${duration(median)}`}
          detail="Approximate median · answered questions"
        />
        <Stat
          label="Open now"
          value={integer(data.open_now.queued + data.open_now.accepted)}
          detail={`${data.open_now.queued} queued · ${data.open_now.accepted} accepted · all dates`}
        />
      </div>
      {!data.daily.length ? (
        <Empty />
      ) : (
        <div className="an-grid">
          <Panel
            wide
            title="Mail activity"
            description="Sent on send day; outcomes on completion day"
            table={
              <Table
                headers={[
                  "Day (UTC)",
                  "Sent",
                  "Answered",
                  "Declined",
                  "Expired",
                  "Cancelled",
                ]}
                rows={daily.map((r) => [
                  r.label,
                  ...r.values.map((v) => integer(v ?? 0)),
                ])}
              />
            }
          >
            <Chart
              rows={daily}
              labels={["Sent", "Answered", "Declined", "Expired", "Cancelled"]}
            />
          </Panel>
          <Panel
            title="Time to answer"
            description="Minutes · answered questions only; gaps mean no answers"
            table={
              <Table
                headers={[
                  "Day (UTC)",
                  "Approx. median",
                  "Approx. p90",
                  "Mean",
                  "Slowest",
                ]}
                rows={data.daily.map((d) => [
                  d.day,
                  d.median_approx_ms === null
                    ? "—"
                    : `≈ ${duration(d.median_approx_ms)}`,
                  d.p90_approx_ms === null
                    ? "—"
                    : `≈ ${duration(d.p90_approx_ms)}`,
                  duration(d.mean_ms),
                  duration(d.slowest_ms),
                ])}
              />
            }
          >
            <Chart rows={times} labels={["Approx. median", "Slowest"]} />
          </Panel>
          <Panel
            title="Frequent conversations"
            description="Questions sent · sender → recipient"
            table={
              <Table
                headers={["Sender", "Recipient", "Questions"]}
                rows={data.top_pairs.map((r) => [
                  name(r.sender),
                  name(r.recipient),
                  integer(r.sent),
                ])}
              />
            }
          >
            <Ranking
              rows={pairs}
              name={(id) => {
                const r = data.top_pairs[Number(id)];
                return `${name(r.sender)} → ${name(r.recipient)}`;
              }}
            />
          </Panel>
          {(["senders", "recipients"] as const).map((kind) => {
            const rows =
              kind === "senders"
                ? data.top_senders.map((r) => ({
                    label: r.session,
                    values: [r.sent],
                  }))
                : data.top_recipients.map((r) => ({
                    label: r.session,
                    values: [r.received],
                  }));
            return (
              <Panel
                key={kind}
                title={
                  kind === "senders" ? "Busiest senders" : "Busiest recipients"
                }
                description="Questions sent in range · top 20 participants"
                table={
                  <Table
                    headers={["Session", "Questions"]}
                    rows={rows.map((r) => [
                      name(r.label),
                      integer(sumValues(r)),
                    ])}
                  />
                }
              >
                <Ranking rows={rows} name={name} />
              </Panel>
            );
          })}
          <Panel
            wide
            title="Broadcasts & Oracle nudges"
            description="Broadcasts count recipient deliveries, not folder actions"
            table={
              <Table
                headers={["Day (UTC)", "Broadcast deliveries", "Oracle nudges"]}
                rows={prompts.map((r) => [
                  r.label,
                  ...r.values.map((v) => integer(v ?? 0)),
                ])}
              />
            }
          >
            <Chart
              rows={prompts}
              labels={["Broadcast deliveries", "Oracle nudges"]}
            />
          </Panel>
        </div>
      )}
      <section className="an-panel an-open">
        <h2>Open now · {data.open_now.queued + data.open_now.accepted}</h2>
        <p>Oldest first · all dates · independent of the selected range</p>
        {data.open_now.oldest.length ? (
          <Table
            headers={["Sender", "Recipient", "Status", "Waiting since (UTC)"]}
            rows={data.open_now.oldest.map((r) => [
              name(r.sender),
              name(r.recipient),
              <span className={`an-status ${r.status}`}>{r.status}</span>,
              new Date(r.created_at)
                .toISOString()
                .replace("T", " ")
                .slice(0, 16),
            ])}
          />
        ) : (
          <p>No open questions.</p>
        )}
        <small>
          Up to the oldest 100 questions. Message text stays in Inbox.
        </small>
      </section>
    </>
  );
}
export function Analytics({
  initialTab,
  sessions,
  onBack,
}: {
  initialTab: AnalyticsTab;
  sessions: SessionView[];
  onBack: () => void;
}) {
  const [tab, setTab] = useState(initialTab),
    [range, setRange] = useState<Range>(() => {
      const saved = localStorage.getItem("duckterm.analytics.range");
      return ["1", "7", "30", "all"].includes(saved ?? "")
        ? (saved as Range)
        : "7";
    });
  const [filters, setFilters] = useState({
      agent: "",
      folder: "",
      session: "",
    }),
    [tokens, setTokens] = useState<TokenAnalytics | null>(null),
    [mail, setMail] = useState<MailAnalytics | null>(null),
    [error, setError] = useState(""),
    [loading, setLoading] = useState(true),
    [refresh, setRefresh] = useState(0);
  useEffect(() => {
    const listener = (e: KeyboardEvent) => {
      if (
        e.key === "Escape" &&
        !(
          e.target instanceof Element &&
          e.target.closest("input,select,textarea")
        )
      )
        onBack();
    };
    window.addEventListener("keydown", listener);
    return () => window.removeEventListener("keydown", listener);
  }, [onBack]);
  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError("");
    const params = new URLSearchParams({
      days: range,
      ...(tab === "tokens" ? filters : {}),
    });
    void loadAnalytics<TokenAnalytics | MailAnalytics>(
      tab,
      params,
      controller.signal,
    )
      .then((data) => {
        if (controller.signal.aborted) return;
        if (tab === "tokens") setTokens(data as TokenAnalytics);
        else setMail(data as MailAnalytics);
        setLoading(false);
      })
      .catch((e: Error) => {
        if (!controller.signal.aborted) {
          setError(e.message);
          setLoading(false);
        }
      });
    return () => controller.abort();
  }, [tab, range, filters, refresh]);
  function changeRange(value: Range) {
    setRange(value);
    localStorage.setItem("duckterm.analytics.range", value);
  }
  return (
    <div className="rd-analytics">
      <div className="an-body">
        <button className="an-back" onClick={onBack}>
          ← Oracle
        </button>
        <div className="an-title">
          <div>
            <h1>Analytics</h1>
            <p>See how your agents work over time.</p>
          </div>
          <span>Local activity · this Mac</span>
        </div>
        <div className="an-toolbar">
          <div
            className="an-tabs"
            role="tablist"
            aria-label="Analytics category"
          >
            {(["tokens", "mail"] as const).map((t) => (
              <button
                key={t}
                role="tab"
                aria-selected={tab === t}
                tabIndex={tab === t ? 0 : -1}
                onClick={() => setTab(t)}
                onKeyDown={(e) => {
                  if (
                    ["ArrowLeft", "ArrowRight", "Home", "End"].includes(e.key)
                  ) {
                    e.preventDefault();
                    const next =
                      e.key === "Home"
                        ? "tokens"
                        : e.key === "End"
                          ? "mail"
                          : t === "tokens"
                            ? "mail"
                            : "tokens";
                    setTab(next);
                    (
                      e.currentTarget.parentElement?.querySelector(
                        `[data-tab="${next}"]`,
                      ) as HTMLButtonElement
                    )?.focus();
                  }
                }}
                data-tab={t}
              >
                {t === "tokens" ? "Tokens" : "Agent Mail"}
              </button>
            ))}
          </div>
          <div className="an-range" aria-label="Time range">
            {(["1", "7", "30", "all"] as const).map((v) => (
              <button
                key={v}
                title={v === "1" ? "Resets at 00:00 UTC" : "UTC calendar days"}
                aria-pressed={range === v}
                onClick={() => changeRange(v)}
              >
                {
                  {
                    "1": "Today (UTC)",
                    "7": "7 days",
                    "30": "30 days",
                    all: "All",
                  }[v]
                }
              </button>
            ))}
          </div>
        </div>
        <div className="an-filters">
          {(["agent", "folder", "session"] as const).map((k) => (
            <label key={k}>
              {k[0].toUpperCase() + k.slice(1)}
              <select
                aria-label={k[0].toUpperCase() + k.slice(1)}
                disabled={tab === "mail"}
                value={tab === "mail" ? "" : filters[k]}
                onChange={(e) =>
                  setFilters({ ...filters, [k]: e.target.value })
                }
              >
                <option value="">All {k}s</option>
                {tokens?.options[`${k}s`].map((v) => (
                  <option key={v.value} value={v.value}>
                    {v.label}
                  </option>
                ))}
              </select>
            </label>
          ))}
          {tab === "tokens" && (
            <button
              onClick={() => setFilters({ agent: "", folder: "", session: "" })}
            >
              Clear filters
            </button>
          )}
          <button
            className="an-refresh"
            onClick={() => setRefresh((n) => n + 1)}
            disabled={loading}
          >
            Refresh
          </button>
        </div>
        <p className="an-context">
          {tab === "tokens"
            ? "Filters apply to every chart below."
            : "Mail shows all local sessions. Filters are not yet available."}{" "}
          UTC calendar days; Today is not a rolling 24 hours.
        </p>
        <div
          role="tabpanel"
          aria-label={
            tab === "tokens" ? "Token analytics" : "Agent Mail analytics"
          }
          aria-busy={loading}
        >
          {loading ? (
            <p role="status" className="an-empty">
              Loading analytics…
            </p>
          ) : error ? (
            <div className="an-empty" role="alert">
              <h2>Couldn’t load analytics</h2>
              <p>{error}</p>
              <button onClick={() => setRefresh((n) => n + 1)}>Retry</button>
            </div>
          ) : tab === "tokens" && tokens ? (
            <Tokens data={tokens} range={range} />
          ) : tab === "mail" && mail ? (
            <Mail data={mail} range={range} sessions={sessions} />
          ) : null}
        </div>
      </div>
    </div>
  );
}
