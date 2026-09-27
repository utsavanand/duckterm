import { beforeEach, expect, it } from "vitest";
import { evenColumns, leaves, resizeSplit } from "./gridLayout";
import { loadGrid, reconcileGrid, saveGrid } from "./gridStorage";

beforeEach(() => localStorage.clear());
it("restores resized stacks and docked sessions separately for each view", () => {
  const saved = { tree: resizeSplit(evenColumns(["a", "b"], 1), [], 0, .3), docked: ["c"] };
  saveGrid("rd.grid.focus", saved);
  expect(loadGrid("rd.grid.focus", ["a", "b", "c"])).toEqual(saved);
  expect(loadGrid("rd.grid.folder.work", ["a", "b", "c"])).not.toEqual(saved);
});
it("removes missing panes and dock entries, keeps arrangements and adds new pins", () => {
  const saved = { tree: evenColumns(["a", "b"], 1), docked: ["c", "gone", "c", "a"] };
  const result = reconcileGrid(saved, ["b", "c", "d"]);
  expect(leaves(result.tree!)).toEqual(["b", "d"]);
  expect(result.docked).toEqual(["c"]);
});
it("recovers malformed storage and duplicate leaves without losing sessions", () => {
  for (const raw of ["{", "null", JSON.stringify({ tree: evenColumns(["a", "a"], 1), docked: [] }),
    JSON.stringify({ tree: { type: "split", dir: "row", children: [{type:"leaf",key:"a"}], weights: [-1] }, docked: [] })]) {
    localStorage.setItem("rd.grid.focus", raw);
    const saved = loadGrid("rd.grid.focus", ["a", "b"]);
    expect(leaves(saved.tree!)).toEqual(["a", "b"]);
  }
});
it("retains an entirely docked arrangement", () => {
  saveGrid("rd.grid.focus", {tree:null, docked:["b", "a"]});
  expect(loadGrid("rd.grid.focus", ["a", "b"])).toEqual({tree:null,docked:["b", "a"]});
});
