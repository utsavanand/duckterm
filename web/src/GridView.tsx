import { useEffect, useMemo, useRef, useState } from "react";
import {
  Edge,
  LayoutNode,
  evenColumns,
  evenRow,
  leaves,
  moveToEdge,
  removeLeaf,
  resizeSplit,
} from "./gridLayout";
import { addPane, loadGrid, reconcileGrid, saveGrid } from "./gridStorage";
import { SessionPin } from "./SessionPin";
import { Terminal } from "./Terminal";
import { SessionView } from "./types";

// Fullscreen grid for ONE folder's terminals (subfolders included). Panes work
// like iTerm/tmux splits: drag a tile's header onto another tile's LEFT/RIGHT
// edge to sit side-by-side, TOP/BOTTOM to stack — the drop zone highlights as
// you hover. Bars between panes drag to resize. Collapsed sessions dock to a
// bottom strip as chips.
export function GridView({
  title,
  agents,
  folders,
  themeFor,
  onSwitchFolder,
  onClose,
  focus = false,
  storageKey,
  onPin,
}: {
  title: string;
  focus?: boolean;
  storageKey?: string;
  onPin?: (session: SessionView) => Promise<void>;
  agents: SessionView[];
  folders: string[];
  themeFor: (s: SessionView) => string;
  onSwitchFolder: (folder: string) => void;
  onClose: () => void;
}) {
  const byKey = useMemo(() => new Map(agents.map((a) => [a.key, a])), [agents]);
  const [initial] = useState(() => loadGrid(storageKey, agents.map(a => a.key)));
  const [tree, setTree] = useState<LayoutNode | null>(initial.tree);
  const [docked, setDocked] = useState<Set<string>>(() => new Set(initial.docked));
  const [dragKey, setDragKey] = useState<string | null>(null);
  const [drop, setDrop] = useState<{ key: string; edge: Edge } | null>(null);
  const rootRef = useRef<HTMLDivElement>(null);
  const agentKeys = JSON.stringify(agents.map(a => a.key).sort());
  useEffect(() => {
    const next = reconcileGrid({ tree, docked: [...docked] }, JSON.parse(agentKeys));
    setTree(next.tree);
    setDocked(new Set(next.docked));
    // Reconcile membership only; layout edits are handled by the controls below.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [agentKeys]);
  useEffect(() => {
    saveGrid(storageKey, reconcileGrid({ tree, docked: [...docked] }, JSON.parse(agentKeys)));
  }, [storageKey, tree, docked, agentKeys]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  function preset(cols: number) {
    const keys = tree ? leaves(tree) : [];
    if (keys.length === 0) return;
    setTree(cols === 0 ? evenRow(keys) : evenColumns(keys, cols));
  }

  function collapse(key: string) {
    setDocked((prev) => new Set(prev).add(key));
    setTree((t) => (t ? removeLeaf(t, key) : t));
  }

  function expand(key: string) {
    setDocked((prev) => {
      const next = new Set(prev);
      next.delete(key);
      return next;
    });
    setTree((t) => addPane(t, key));
  }

  function edgeFor(e: React.DragEvent, el: HTMLElement): Edge {
    const r = el.getBoundingClientRect();
    const x = (e.clientX - r.left) / r.width;
    const y = (e.clientY - r.top) / r.height;
    const d: [Edge, number][] = [
      ["left", x],
      ["right", 1 - x],
      ["top", y],
      ["bottom", 1 - y],
    ];
    d.sort((a, b) => a[1] - b[1]);
    return d[0][0];
  }

  function startResize(
    e: React.PointerEvent,
    dir: "row" | "col",
    path: number[],
    index: number,
  ) {
    e.preventDefault();
    e.stopPropagation();
    const container = rootRef.current;
    if (!container) return;
    const rect = container.getBoundingClientRect();
    const px = dir === "row" ? rect.width : rect.height;
    let last = dir === "row" ? e.clientX : e.clientY;
    const onMove = (ev: PointerEvent) => {
      const pos = dir === "row" ? ev.clientX : ev.clientY;
      // Pixel movement scaled into weight units against the full container —
      // approximate for nested splits, but the drag FEELS direct, which is
      // what matters.
      const delta = ((pos - last) / px) * 3;
      last = pos;
      setTree((t) => (t ? resizeSplit(t, path, index, delta) : t));
    };
    const onUp = () => {
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
    };
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
  }

  function renderNode(node: LayoutNode, path: number[]): JSX.Element | null {
    if (node.type === "leaf") {
      const s = byKey.get(node.key);
      if (!s) return null;
      const isDrop = drop?.key === node.key;
      return (
        <div
          className="rd-grid-tile"
          onDragOver={(e) => {
            if (!dragKey || dragKey === node.key) return;
            e.preventDefault();
            setDrop({ key: node.key, edge: edgeFor(e, e.currentTarget) });
          }}
          onDragLeave={() => setDrop((d) => (d?.key === node.key ? null : d))}
          onDrop={(e) => {
            e.preventDefault();
            if (dragKey && drop?.key === node.key) {
              setTree((t) =>
                t ? moveToEdge(t, dragKey, node.key, drop.edge) : t,
              );
            }
            setDragKey(null);
            setDrop(null);
          }}
        >
          {isDrop && <div className={`rd-grid-dropzone ${drop!.edge}`} />}
          <div
            className="rd-grid-tile-head"
            draggable
            title="Drag onto another tile's edge — left/right sits beside it, top/bottom stacks"
            onDragStart={() => setDragKey(node.key)}
            onDragEnd={() => {
              setDragKey(null);
              setDrop(null);
            }}
          >
            <span className={`rd-state st-${s.state}`}>
              <span className="dot" />
            </span>
            <span className="rd-grid-tile-name">{s.label}</span>
            {s.branch && (
              <span className="rd-grid-tile-branch">⎇ {s.branch}</span>
            )}
            <span className="rd-spacer" />
            {focus && onPin && <SessionPin session={s} onToggle={onPin} />}
            <button
              className="rd-grid-tile-collapse"
              title="Collapse to the dock"
              onClick={() => collapse(node.key)}
            >
              ▾
            </button>
          </div>
          <div className="rd-grid-tile-term">
            {focus && (["stopped", "archived", "terminated", "interrupted"].includes(s.state) || !s.ptyOwned) ? (
              <p className="rd-focus-at-rest">{["stopped", "archived", "terminated", "interrupted"].includes(s.state)
                ? `${s.label} is ${s.state}. Its pin is saved.`
                : "This session has no terminal in DuckTerm."}</p>
            ) : <Terminal sessionKey={node.key} theme={themeFor(s)} />}
          </div>
        </div>
      );
    }
    const total = node.weights.reduce((a, b) => a + b, 0);
    return (
      <div className={`rd-grid-split ${node.dir}`}>
        {node.children.map((child, i) => (
          <div
            key={child.type === "leaf" ? child.key : `s${i}`}
            className="rd-grid-cell"
            style={{ flexGrow: node.weights[i] / total }}
          >
            {renderNode(child, [...path, i])}
            {i < node.children.length - 1 && (
              <div
                className={
                  node.dir === "row" ? "rd-grid-split-v" : "rd-grid-split-h"
                }
                title="Drag to resize"
                onPointerDown={(e) => startResize(e, node.dir, path, i)}
              />
            )}
          </div>
        ))}
      </div>
    );
  }

  const dockedList = agents.filter((a) => docked.has(a.key));
  const shown = tree ? leaves(tree).length : 0;

  return (
    <div className="rd-grid">
      <div className="rd-grid-bar">
        <span className="rd-grid-title">
          {focus ? `Focus · ${agents.length} / 3 pinned` : `${shown + dockedList.length} running`}
        </span>
        {!focus && <select
          className="rd-grid-folder"
          value={title}
          onChange={(e) => onSwitchFolder(e.target.value)}
        >
          {!folders.includes(title) && <option value={title}>{title}</option>}
          {folders.map((f) => (
            <option key={f} value={f}>
              {f}
            </option>
          ))}
        </select>}
        <select
          className="rd-grid-folder rd-grid-cols"
          title="Reset the arrangement to an even layout"
          value=""
          onChange={(e) => {
            if (e.target.value !== "") preset(Number(e.target.value));
          }}
        >
          <option value="">reset layout…</option>
          <option value={0}>side by side</option>
          <option value={1}>1 column</option>
          <option value={2}>2 columns</option>
          <option value={3}>3 columns</option>
          {!focus && <option value={4}>4 columns</option>}
        </select>
        <span className="rd-grid-hint">
          drag a title bar onto a tile's edge to split
        </span>
        <span className="rd-spacer" />
        <button className="rd-btn rd-btn-sm rd-btn-ghost" onClick={onClose}>
          {focus ? "← Sessions (esc)" : "Exit grid (esc)"}
        </button>
      </div>
      {!tree ? (
        <p className="rd-panel-empty">
          {dockedList.length > 0
            ? "Every session is collapsed — click a chip below to expand it."
            : focus ? "No pinned sessions. Return to Sessions to pin one." : "No running terminals in this folder."}
        </p>
      ) : (
        <div ref={rootRef} className="rd-grid-tiles-tree">
          {renderNode(tree, [])}
        </div>
      )}
      {dockedList.length > 0 && (
        <div className="rd-grid-dock">
          {dockedList.map((s) => (
            <button
              key={s.key}
              className="rd-grid-dock-chip"
              title="Bring back into the grid"
              onClick={() => expand(s.key)}
            >
              <span className={`rd-state st-${s.state}`}>
                <span className="dot" />
              </span>
              {s.label}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
