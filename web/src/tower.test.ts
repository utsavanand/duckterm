import { describe, expect, it } from "vitest";
import { facts, looksStuck, placement, teams, TowerAgent } from "./tower";

const NOW = 10_000_000_000;
function agent(p: Partial<TowerAgent> & { key: string }): TowerAgent {
  return { label: p.key, state: "idle", shownState: "idle", lastEventType: "", startedAt: 0, updatedAt: NOW, eventCount: 0, ...p } as TowerAgent;
}

describe("teams", () => {
  it("groups by top-level folder, largest team first, ungrouped as No folder", () => {
    const t = teams([
      agent({ key: "a", group: "Duckterm" }),
      agent({ key: "b", group: "Duckterm/ui" }),
      agent({ key: "c", group: "Nourish" }),
      agent({ key: "d" }),
    ]);
    expect(t.map((x) => [x.name, x.agents.length])).toEqual([["Duckterm", 2], ["No folder", 1], ["Nourish", 1]]);
  });
});

describe("looksStuck", () => {
  it("flags busy sessions with no event for over 30 minutes only", () => {
    expect(looksStuck(agent({ key: "a", shownState: "busy", updatedAt: NOW - 31 * 60_000 }), NOW)).toBe(true);
    expect(looksStuck(agent({ key: "a", shownState: "busy", updatedAt: NOW - 29 * 60_000 }), NOW)).toBe(false);
    expect(looksStuck(agent({ key: "a", shownState: "idle", updatedAt: NOW - 3 * 86_400_000 }), NOW)).toBe(false);
  });
});

describe("facts", () => {
  it("names the largest context and running sub-agents, and skips facts that aren't true", () => {
    const f = facts([
      agent({ key: "a", group: "X", contextTokens: 650_000, subagents: [{ agent_id: "1", state: "running", started_at: 0 }, { agent_id: "2", state: "done", started_at: 0 }] }),
      agent({ key: "b", group: "X", contextTokens: 100_000 }),
    ], NOW);
    expect(f).toEqual([
      { label: "Largest context", value: "a, 650k tokens" },
      { label: "Most sub-agents", value: "a, 1" },
    ]);
  });
});

describe("placement", () => {
  it("keeps every pair of ducks at least a duck apart for teams up to 12", () => {
    for (let n = 1; n <= 12; n++) {
      const keys = Array.from({ length: n }, (_, i) => `session-${n}-${i}`);
      const { rows, spots } = placement(keys);
      const width = n > 4 ? 640 : 320;
      for (let i = 0; i < n; i++)
        for (let j = i + 1; j < n; j++) {
          const dx = Math.abs(spots[i].left - spots[j].left) / 100 * width;
          const dy = Math.abs(spots[i].top - spots[j].top);
          expect(dx >= 60 || dy >= 70).toBe(true);
        }
      expect(Math.max(...spots.map((s) => s.top))).toBeLessThan(rows * 88);
    }
  });
});
