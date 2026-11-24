import { expect, it } from "vitest";
import { adjust, appended, boxOf, homeDefaults, settle } from "./homeGrid";

const at = (instances: ReturnType<typeof homeDefaults>, id: string) => boxOf(instances.find(i => i.id === id)!);

it("pushes the widget a drag lands on downwards and leaves the rest where they are", () => {
  // Drag Teams (0,0 3x2) onto Needs you (0,4 3x4).
  const moved = adjust(homeDefaults(), "teams", at(homeDefaults(), "teams"), "move", 0, 4);
  expect(at(moved, "teams")).toEqual({ x: 0, y: 4, w: 3, h: 2 });
  expect(at(moved, "needs-you")).toEqual({ x: 0, y: 6, w: 3, h: 4 });
  // Autopilot keeps its row; the gap Teams left stays open.
  expect(at(moved, "autopilot")).toEqual({ x: 0, y: 2, w: 3, h: 2 });
  // Widgets in other columns are untouched.
  expect(at(moved, "chat")).toEqual({ x: 3, y: 0, w: 6, h: 8 });
});

it("keeps moves on the grid and resizes above each widget's minimum", () => {
  const start = at(homeDefaults(), "chat");
  expect(at(adjust(homeDefaults(), "chat", start, "move", 20, -5), "chat")).toEqual({ x: 6, y: 0, w: 6, h: 8 });
  expect(at(adjust(homeDefaults(), "chat", start, "resize", -10, -10), "chat")).toEqual({ x: 3, y: 0, w: 4, h: 5 });
  expect(at(adjust(homeDefaults(), "chat", start, "resize", 10, 0), "chat").w).toBe(9);
});

it("adds a widget below everything else at its default size", () => {
  const added = appended(homeDefaults(), "mail");
  expect(at(added, "mail")).toEqual({ x: 0, y: 8, w: 3, h: 2 });
  expect(settle(added).map(boxOf)).toEqual(added.map(boxOf));
});
