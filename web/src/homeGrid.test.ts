import { expect, it } from "vitest";
import { adjust, appended, boxOf, homeDefaults, settle } from "./homeGrid";

const at = (instances: ReturnType<typeof homeDefaults>, id: string) => boxOf(instances.find(i => i.id === id)!);

it("pushes the widget a drag lands on downwards and leaves the rest where they are", () => {
  // Drag Teams (0,0 3x1) onto Spend (0,2 3x5); Spend pushes Pulse down in turn.
  const moved = adjust(homeDefaults(), "teams", at(homeDefaults(), "teams"), "move", 0, 2);
  expect(at(moved, "teams")).toEqual({ x: 0, y: 2, w: 3, h: 1 });
  expect(at(moved, "spend")).toEqual({ x: 0, y: 3, w: 3, h: 5 });
  expect(at(moved, "pulse")).toEqual({ x: 0, y: 8, w: 3, h: 5 });
  // Autopilot keeps its row; the gap Teams left stays open.
  expect(at(moved, "autopilot")).toEqual({ x: 0, y: 1, w: 3, h: 1 });
  // Widgets in other columns are untouched.
  expect(at(moved, "prs")).toEqual({ x: 3, y: 0, w: 5, h: 6 });
  expect(at(moved, "chat")).toEqual({ x: 8, y: 0, w: 4, h: 12 });
});

it("keeps moves on the grid and resizes above each widget's minimum", () => {
  const start = at(homeDefaults(), "prs");
  expect(at(adjust(homeDefaults(), "prs", start, "move", 20, -5), "prs")).toEqual({ x: 7, y: 0, w: 5, h: 6 });
  expect(at(adjust(homeDefaults(), "prs", start, "resize", -10, -10), "prs")).toEqual({ x: 3, y: 0, w: 3, h: 3 });
  expect(at(adjust(homeDefaults(), "prs", start, "resize", 10, 0), "prs").w).toBe(9);
});

it("adds a widget below everything else at its default size", () => {
  const added = appended(homeDefaults(), "mail");
  expect(at(added, "mail")).toEqual({ x: 0, y: 12, w: 3, h: 2 });
  expect(settle(added).map(boxOf)).toEqual(added.map(boxOf));
});
