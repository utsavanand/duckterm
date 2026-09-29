import { describe, it, expect } from "vitest";
import {
  groupTokens,
  fillDays,
  mailMedian,
  TokenRow,
  MailAnalytics,
  windowStart,
} from "./analyticsData";
const row: TokenRow = {
  day: "2026-09-28",
  model: "gpt-6-astra",
  agent: "codex",
  session: "one",
  session_name: "Builder",
  folder: "A",
  folder_name: "A",
  input: 100,
  cache_read: 200,
  cache_write: 0,
  output: 10,
};
describe("analytics dimensions and missing data", () => {
  it("keeps exact model identifiers distinct and preserves unreported usage", () => {
    const groups = groupTokens(
      [row, { ...row, model: "gpt-6-sol" }, { ...row, model: null }],
      "model",
    );
    expect(groups.map((r) => r.label).sort()).toEqual([
      "Model not reported",
      "gpt-6-astra",
      "gpt-6-sol",
    ]);
  });
  it("fills known days but never fabricates pre-history activity", () => {
    expect(windowStart("30", "2026-09-28", "2026-09-22")).toBe("2026-09-22");
    expect(
      fillDays(
        [{ label: "2026-09-24", values: [5] }],
        "2026-09-23",
        "2026-09-25",
        1,
      ),
    ).toEqual([
      { label: "2026-09-23", values: [0] },
      { label: "2026-09-24", values: [5] },
      { label: "2026-09-25", values: [0] },
    ]);
    expect(fillDays([], "2026-09-28", "2026-09-28", 2, null)[0].values).toEqual(
      [null, null],
    );
  });
  it("combines response histograms rather than averaging daily medians", () => {
    const data = {
      histogram_upper_bounds_ms: [60000, 300000, null],
      daily: [
        { histogram: [99, 0, 0], slowest_ms: 50000 },
        { histogram: [0, 0, 1], slowest_ms: 900000 },
      ],
    } as MailAnalytics;
    expect(mailMedian(data)).toBeCloseTo(30303.03, 2);
    expect(mailMedian({ ...data, daily: [] })).toBeNull();
  });
});
