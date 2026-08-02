import { describe, expect, it } from "vitest";
import {
  evenColumns,
  evenRow,
  insertAtEdge,
  leaves,
  moveToEdge,
  removeLeaf,
  resizeSplit,
} from "./gridLayout";

describe("gridLayout split tree", () => {
  it("evenRow lays sessions out as vertical sections", () => {
    const t = evenRow(["a", "b", "c"]);
    expect(t).toEqual({
      type: "split",
      dir: "row",
      children: [
        { type: "leaf", key: "a" },
        { type: "leaf", key: "b" },
        { type: "leaf", key: "c" },
      ],
      weights: [1, 1, 1],
    });
  });

  it("evenColumns wraps overflow into stacks", () => {
    const t = evenColumns(["a", "b", "c"], 2);
    // 2 columns: [a,c] stacked, [b] alone.
    expect(leaves(t)).toEqual(["a", "c", "b"]);
    if (t.type !== "split") throw new Error("expected split");
    expect(t.dir).toBe("row");
    expect(t.children[0]).toMatchObject({ type: "split", dir: "col" });
  });

  it("dropping on a cross-axis edge wraps the target in a new split", () => {
    // a|b side by side; drop b on a's BOTTOM -> a stacked over b.
    const t = moveToEdge(evenRow(["a", "b"]), "b", "a", "bottom");
    expect(t).toEqual({
      type: "split",
      dir: "col",
      children: [
        { type: "leaf", key: "a" },
        { type: "leaf", key: "b" },
      ],
      weights: [1, 1],
    });
  });

  it("dropping on a same-axis edge joins as a sibling taking half the share", () => {
    const t = insertAtEdge(evenRow(["a", "b"]), "a", "c", "left");
    if (t.type !== "split") throw new Error("expected split");
    expect(leaves(t)).toEqual(["c", "a", "b"]);
    expect(t.weights).toEqual([0.5, 0.5, 1]); // c took half of a's share
  });

  it("a vertical stack becomes side-by-side by dragging to a side edge", () => {
    // The user's exact complaint: stacked -> horizontal.
    const stacked = moveToEdge(evenRow(["a", "b"]), "b", "a", "bottom");
    const sideBySide = moveToEdge(stacked, "b", "a", "right");
    expect(sideBySide).toMatchObject({ type: "split", dir: "row" });
    expect(leaves(sideBySide)).toEqual(["a", "b"]);
  });

  it("removeLeaf collapses single-child splits", () => {
    const t = evenRow(["a", "b"]);
    expect(removeLeaf(t, "a")).toEqual({ type: "leaf", key: "b" });
    expect(removeLeaf({ type: "leaf", key: "a" }, "a")).toBeNull();
  });

  it("moveToEdge onto itself or a missing target is a no-op", () => {
    const t = evenRow(["a", "b"]);
    expect(moveToEdge(t, "a", "a", "left")).toBe(t);
    expect(moveToEdge(t, "a", "zz", "left")).toBe(t);
  });

  it("resizeSplit shifts weight between neighbours and clamps at 0.15", () => {
    const t = evenRow(["a", "b"]);
    const grown = resizeSplit(t, [], 0, 0.5);
    if (grown.type !== "split") throw new Error("expected split");
    expect(grown.weights).toEqual([1.5, 0.5]);
    const clamped = resizeSplit(t, [], 0, 99);
    if (clamped.type !== "split") throw new Error("expected split");
    expect(clamped.weights[1]).toBeCloseTo(0.15);
  });
});
