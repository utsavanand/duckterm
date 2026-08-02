// The grid's layout model: a split tree, like tmux panes. A node is either a
// session tile (leaf) or a split holding children side-by-side ("row") or
// stacked ("col"), each child sized by a weight. Dropping a tile on another
// tile's EDGE restructures the tree — left/right makes them side-by-side,
// top/bottom stacks them — which is what a flat cols×rows grid can't express.

export type Edge = "left" | "right" | "top" | "bottom";

export type LayoutNode =
  | { type: "leaf"; key: string }
  | {
      type: "split";
      dir: "row" | "col";
      children: LayoutNode[];
      weights: number[];
    };

export function leaf(key: string): LayoutNode {
  return { type: "leaf", key };
}

/** Default: one row of vertical sections. */
export function evenRow(keys: string[]): LayoutNode {
  if (keys.length === 1) return leaf(keys[0]);
  return {
    type: "split",
    dir: "row",
    children: keys.map(leaf),
    weights: keys.map(() => 1),
  };
}

/** Even N-column preset: columns side-by-side, overflow stacking downward. */
export function evenColumns(keys: string[], cols: number): LayoutNode {
  const buckets: string[][] = Array.from({ length: cols }, () => []);
  keys.forEach((k, i) => buckets[i % cols].push(k));
  const used = buckets.filter((b) => b.length > 0);
  if (used.length === 1) return stack(used[0]);
  return {
    type: "split",
    dir: "row",
    children: used.map(stack),
    weights: used.map(() => 1),
  };
}

function stack(keys: string[]): LayoutNode {
  if (keys.length === 1) return leaf(keys[0]);
  return {
    type: "split",
    dir: "col",
    children: keys.map(leaf),
    weights: keys.map(() => 1),
  };
}

export function leaves(node: LayoutNode): string[] {
  if (node.type === "leaf") return [node.key];
  return node.children.flatMap(leaves);
}

/** Remove a tile; single-child splits collapse away. Returns null when empty. */
export function removeLeaf(node: LayoutNode, key: string): LayoutNode | null {
  if (node.type === "leaf") return node.key === key ? null : node;
  const children: LayoutNode[] = [];
  const weights: number[] = [];
  node.children.forEach((child, i) => {
    const kept = removeLeaf(child, key);
    if (kept) {
      children.push(kept);
      weights.push(node.weights[i]);
    }
  });
  if (children.length === 0) return null;
  if (children.length === 1) return children[0];
  return { ...node, children, weights };
}

/** Insert `key` at an edge of `targetKey`, restructuring like a tmux split:
 *  along the parent's own axis it becomes a sibling; across it, the target
 *  leaf is wrapped in a new split of the other direction. */
export function insertAtEdge(
  node: LayoutNode,
  targetKey: string,
  key: string,
  edge: Edge,
): LayoutNode {
  const dir: "row" | "col" =
    edge === "left" || edge === "right" ? "row" : "col";
  const before = edge === "left" || edge === "top";

  function walk(n: LayoutNode): LayoutNode {
    if (n.type === "leaf") {
      if (n.key !== targetKey) return n;
      const pair = before ? [leaf(key), n] : [n, leaf(key)];
      return { type: "split", dir, children: pair, weights: [1, 1] };
    }
    const idx = n.children.findIndex(
      (c) => c.type === "leaf" && c.key === targetKey,
    );
    if (idx !== -1 && n.dir === dir) {
      // Same axis: join as a sibling, taking half the target's share.
      const children = [...n.children];
      const weights = [...n.weights];
      const share = weights[idx] / 2;
      weights[idx] = share;
      children.splice(before ? idx : idx + 1, 0, leaf(key));
      weights.splice(before ? idx : idx + 1, 0, share);
      return { ...n, children, weights };
    }
    return { ...n, children: n.children.map(walk) };
  }
  return walk(node);
}

/** Move an existing tile onto another tile's edge (no-op onto itself). */
export function moveToEdge(
  node: LayoutNode,
  key: string,
  targetKey: string,
  edge: Edge,
): LayoutNode {
  if (key === targetKey) return node;
  const without = removeLeaf(node, key);
  if (!without || !leaves(without).includes(targetKey)) return node;
  return insertAtEdge(without, targetKey, key, edge);
}

/** Adjust the weights either side of splitter `index` inside the split found
 *  at `path` (child indexes from the root). Weights never drop below 0.15. */
export function resizeSplit(
  node: LayoutNode,
  path: number[],
  index: number,
  delta: number,
): LayoutNode {
  if (path.length === 0) {
    if (node.type !== "split") return node;
    const weights = [...node.weights];
    const moved = Math.max(
      -(weights[index] - 0.15),
      Math.min(delta, weights[index + 1] - 0.15),
    );
    weights[index] += moved;
    weights[index + 1] -= moved;
    return { ...node, weights };
  }
  if (node.type !== "split") return node;
  const [head, ...rest] = path;
  const children = [...node.children];
  children[head] = resizeSplit(children[head], rest, index, delta);
  return { ...node, children };
}
