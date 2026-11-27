import { WidgetInstance } from "./Widgets";

// Mirrors HOME_DEFAULTS in src/duckterm/persistence/layouts.py.
export const COLUMNS = 12;
// Rows the default layout spans; Home sizes rows so these fill the page.
export const ROWS = 12;
export const MIN_ROW = 44;
export interface Box { x: number; y: number; w: number; h: number }
const DEFAULTS: [string, number, number, number, number][] = [
  ["teams", 0, 0, 3, 1], ["autopilot", 0, 1, 3, 1], ["spend", 0, 2, 3, 5], ["pulse", 0, 7, 3, 5],
  ["prs", 3, 0, 5, 6], ["needs-you", 3, 6, 5, 6], ["chat", 8, 0, 4, 12],
];
// Size a widget gets when added, and the smallest it can be resized to.
export const SIZES: Record<string, { w: number; h: number; minW: number; minH: number }> = {
  teams: { w: 3, h: 1, minW: 2, minH: 1 }, autopilot: { w: 3, h: 1, minW: 2, minH: 1 },
  chat: { w: 6, h: 6, minW: 4, minH: 4 }, prs: { w: 3, h: 12, minW: 3, minH: 3 },
  spend: { w: 3, h: 5, minW: 2, minH: 3 }, pulse: { w: 3, h: 5, minW: 2, minH: 3 }, usage: { w: 3, h: 3, minW: 2, minH: 2 }, "needs-you": { w: 6, h: 6, minW: 3, minH: 3 },
  shortcuts: { w: 6, h: 2, minW: 3, minH: 2 },
  agents: { w: 3, h: 3, minW: 2, minH: 2 }, mail: { w: 3, h: 2, minW: 2, minH: 2 },
  backup: { w: 3, h: 2, minW: 2, minH: 2 }, remote: { w: 3, h: 2, minW: 2, minH: 2 },
};

export function homeDefaults(): WidgetInstance[] {
  return DEFAULTS.map(([type, x, y, w, h], position) => ({ id: type, type, slot: "home", position, params: box({ x, y, w, h }) }));
}

// Params travel as the server's {x, y, w, h} integers; WidgetInstance types them as strings.
export function boxOf(instance: WidgetInstance): Box {
  const p = instance.params as unknown as Box;
  return { x: p.x, y: p.y, w: p.w, h: p.h };
}
export function box(b: Box): Record<string, string> { return b as unknown as Record<string, string>; }

const overlaps = (a: Box, b: Box) => a.x < b.x + b.w && b.x < a.x + a.w && a.y < b.y + b.h && b.y < a.y + a.h;

/** Widgets stay where they were put. The pinned one keeps its box; any widget it
 * lands on moves down until it fits, and so on down the page. Gaps are allowed:
 * pulling widgets up would undo a move down by one row. */
export function settle(instances: WidgetInstance[], pinned?: string): WidgetInstance[] {
  const placed: Box[] = [];
  const fixed = instances.find(item => item.id === pinned);
  if (fixed) placed.push(boxOf(fixed));
  const order = instances.filter(item => item !== fixed).sort((a, b) => boxOf(a).y - boxOf(b).y || boxOf(a).x - boxOf(b).x);
  const settled = new Map<string, Box>(fixed ? [[fixed.id, boxOf(fixed)]] : []);
  for (const item of order) {
    const next = boxOf(item);
    while (placed.some(other => overlaps(other, next))) next.y++;
    placed.push(next); settled.set(item.id, next);
  }
  return instances.map(item => ({ ...item, params: box(settled.get(item.id)!) }));
}

/** Move or resize one widget by whole cells, clamped to the grid and the widget's minimum size. */
export function adjust(instances: WidgetInstance[], id: string, start: Box, mode: "move" | "resize", dx: number, dy: number): WidgetInstance[] {
  const item = instances.find(i => i.id === id);
  if (!item) return instances;
  const size = SIZES[item.type] ?? { minW: 2, minH: 2 };
  const next = mode === "move"
    ? { ...start, x: Math.max(0, Math.min(COLUMNS - start.w, start.x + dx)), y: Math.max(0, start.y + dy) }
    : { ...start, w: Math.max(size.minW, Math.min(COLUMNS - start.x, start.w + dx)), h: Math.max(size.minH, Math.min(24, start.h + dy)) };
  return settle(instances.map(i => i.id === id ? { ...i, params: box(next) } : i), id);
}

export function appended(instances: WidgetInstance[], type: string): WidgetInstance[] {
  const size = SIZES[type] ?? { w: 3, h: 3 };
  const bottom = Math.max(0, ...instances.map(i => boxOf(i).y + boxOf(i).h));
  return [...instances, { id: type, type, slot: "home", position: instances.length, params: box({ x: 0, y: bottom, w: size.w, h: size.h }) }];
}

/** Reading order, so keyboard and screen-reader order follow the grid. */
export function inReadingOrder(instances: WidgetInstance[]): WidgetInstance[] {
  return [...instances].sort((a, b) => boxOf(a).y - boxOf(b).y || boxOf(a).x - boxOf(b).x);
}
