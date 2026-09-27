import { evenRow, LayoutNode, leaves, removeLeaf } from "./gridLayout";
export interface SavedGrid { tree: LayoutNode | null; docked: string[] }
export function addPane(node: LayoutNode | null, key: string): LayoutNode {
  return node ? { type: "split", dir: "row", children: [node, { type: "leaf", key }], weights: [Math.max(1, leaves(node).length), 1] } : evenRow([key]);
}
function validTree(value: unknown, seen: Set<string>, depth = 0): value is LayoutNode {
  if (!value || typeof value !== "object" || depth > 20 || seen.size > 500) return false;
  const n = value as Record<string, unknown>;
  if (n.type === "leaf") {
    if (typeof n.key !== "string" || !n.key || seen.has(n.key)) return false;
    seen.add(n.key); return true;
  }
  return n.type === "split" && (n.dir === "row" || n.dir === "col") &&
    Array.isArray(n.children) && n.children.length >= 2 && n.children.length <= 500 &&
    Array.isArray(n.weights) && n.weights.length === n.children.length &&
    n.weights.every(w => typeof w === "number" && Number.isFinite(w) && w > 0 && w < 1e6) &&
    n.children.every(c => validTree(c, seen, depth + 1));
}
export function reconcileGrid(saved: SavedGrid, keys: string[]): SavedGrid {
  const allowed = new Set(keys);
  let tree = saved.tree;
  for (const key of tree ? leaves(tree) : []) if (!allowed.has(key)) tree = tree ? removeLeaf(tree, key) : null;
  const visible = new Set(tree ? leaves(tree) : []);
  const docked = [...new Set(saved.docked)].filter(key => allowed.has(key) && !visible.has(key));
  const known = new Set([...visible, ...docked]);
  for (const key of keys) if (!known.has(key)) { tree = addPane(tree, key); known.add(key); }
  return { tree, docked };
}
export function loadGrid(storageKey: string | undefined, keys: string[]): SavedGrid {
  const fallback = { tree: keys.length ? evenRow(keys.slice(0, 3)) : null, docked: keys.slice(3) };
  if (!storageKey) return fallback;
  try {
    const raw = localStorage.getItem(storageKey);
    if (!raw || raw.length > 100_000) return fallback;
    const saved = JSON.parse(raw);
    if (!saved || !Array.isArray(saved.docked) || saved.docked.length > 500 ||
      !saved.docked.every((k: unknown) => typeof k === "string") ||
      (saved.tree !== null && !validTree(saved.tree, new Set()))) return fallback;
    return reconcileGrid(saved, keys);
  } catch { return fallback; }
}
export function saveGrid(storageKey: string | undefined, saved: SavedGrid) {
  if (!storageKey) return;
  try { localStorage.setItem(storageKey, JSON.stringify(saved)); } catch { /* Optional storage. */ }
}
