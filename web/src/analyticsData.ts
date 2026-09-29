import { authHeaders, TokenTotals } from "./api";
export type AnalyticsTab = "tokens" | "mail";
export type Range = "1" | "7" | "30" | "all";
export interface TokenRow extends TokenTotals {
  day: string;
  model: string | null;
  agent: string;
  session: string;
  session_name: string;
  folder: string;
  folder_name: string;
}
export interface TokenAnalytics {
  version: number;
  timezone: string;
  days: number | null;
  today: string;
  earliest_day: string | null;
  rows: TokenRow[];
  options: Record<
    "agents" | "folders" | "sessions",
    { value: string; label: string }[]
  >;
}
export interface MailDay {
  day: string;
  sent: number;
  answered: number;
  declined: number;
  expired: number;
  cancelled: number;
  broadcasts: number;
  nudges: number;
  duration_count: number;
  duration_sum_ms: number;
  slowest_ms: number | null;
  mean_ms: number | null;
  median_approx_ms: number | null;
  p90_approx_ms: number | null;
  histogram: number[];
}
export interface MailAnalytics {
  enabled_at: number;
  daily: MailDay[];
  histogram_upper_bounds_ms: (number | null)[];
  top_senders: { session: string; sent: number }[];
  top_recipients: { session: string; received: number }[];
  top_pairs: { sender: string; recipient: string; sent: number }[];
  open_now: {
    queued: number;
    accepted: number;
    oldest: {
      id: string;
      sender: string;
      recipient: string;
      status: string;
      created_at: number;
    }[];
  };
}
export async function loadAnalytics<T>(
  tab: AnalyticsTab,
  params: URLSearchParams,
  signal: AbortSignal,
): Promise<T> {
  const res = await fetch(`/analytics/${tab}?${params}`, {
    headers: authHeaders(),
    cache: "no-store",
    signal,
  });
  const body = await res.json().catch(() => ({}));
  if (!res.ok)
    throw new Error(body.error ?? `Analytics request failed (${res.status})`);
  return body as T;
}
export const tokenTotal = (r: TokenTotals) =>
  r.input + r.cache_read + r.cache_write + r.output;
export interface SeriesRow {
  label: string;
  values: (number | null)[];
}
export function groupTokens(
  rows: TokenRow[],
  field: "day" | "model" | "agent" | "session" | "folder",
): SeriesRow[] {
  const result = new Map<string, number[]>();
  for (const row of rows) {
    const key = row[field] ?? "Model not reported";
    const values = result.get(key) ?? [0, 0, 0, 0];
    [row.input, row.cache_read, row.cache_write, row.output].forEach((n, i) => {
      values[i] += n;
    });
    result.set(key, values);
  }
  return [...result]
    .map(([label, values]) => ({ label, values }))
    .sort((a, b) =>
      field === "day"
        ? a.label.localeCompare(b.label)
        : sumValues(b) - sumValues(a),
    );
}
export const sumValues = (r: SeriesRow) =>
  r.values.reduce<number>((n, v) => n + (v ?? 0), 0);
export function fillDays(
  rows: SeriesRow[],
  start: string,
  end: string,
  columns: number,
  missing: number | null = 0,
): SeriesRow[] {
  const found = new Map(rows.map((r) => [r.label, r]));
  const result: SeriesRow[] = [];
  for (
    let time = Date.parse(`${start}T00:00:00Z`);
    time <= Date.parse(`${end}T00:00:00Z`);
    time += 86400000
  ) {
    const day = new Date(time).toISOString().slice(0, 10);
    result.push(
      found.get(day) ?? { label: day, values: Array(columns).fill(missing) },
    );
  }
  return result;
}
export function windowStart(
  range: Range,
  today: string,
  earliest: string,
): string {
  if (range === "all") return earliest;
  const requested = new Date(
    Date.parse(today + "T00:00:00Z") - (Number(range) - 1) * 86400000,
  )
    .toISOString()
    .slice(0, 10);
  return requested > earliest ? requested : earliest;
}
// Combine histograms, never average the daily medians. Keep null when no answers.
export function mailMedian(data: MailAnalytics): number | null {
  const hist = Array<number>(data.histogram_upper_bounds_ms.length).fill(0);
  let maximum = 0;
  for (const day of data.daily) {
    day.histogram.forEach((n, i) => {
      hist[i] += n;
    });
    maximum = Math.max(maximum, day.slowest_ms ?? 0);
  }
  const count = hist.reduce((a, b) => a + b, 0);
  if (!count) return null;
  let seen = 0;
  for (let i = 0; i < hist.length; i++) {
    if (hist[i] && seen + hist[i] >= count / 2) {
      const low = i ? (data.histogram_upper_bounds_ms[i - 1] ?? 0) : 0;
      const high = data.histogram_upper_bounds_ms[i] ?? Math.max(maximum, low);
      return low + ((high - low) * (count / 2 - seen)) / hist[i];
    }
    seen += hist[i];
  }
  return null;
}
