import { describe, expect, it } from "vitest";
import { retainTerminals } from "./terminalCache";

describe("recent terminal subscriptions", () => {
  const keys = ["a", "b", "c", "d", "e"];
  it("does not subscribe to unvisited sessions", () => {
    expect(retainTerminals([], keys, null)).toEqual([]);
    expect(retainTerminals([], keys, "c")).toEqual(["c"]);
  });
  it("keeps three recent views, promotes a revisit and evicts the oldest", () => {
    let recent: string[] = [];
    for (const key of ["a", "b", "c", "a", "d"]) recent = retainTerminals(recent, keys, key);
    expect(recent).toEqual(["d", "a", "c"]);
    expect(retainTerminals(recent, keys, "d")).toBe(recent);
  });
  it("removes deleted sessions and keeps recent views on nonterminal tabs", () => {
    expect(retainTerminals(["c", "b", "a"], ["a", "b"], "c")).toEqual(["b", "a"]);
    expect(retainTerminals(["b", "a"], keys, null)).toEqual(["b", "a"]);
  });
});
