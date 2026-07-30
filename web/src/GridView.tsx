import { useEffect, useMemo, useRef, useState } from "react";
import { Terminal } from "./Terminal";
import { SessionView } from "./types";

// Fullscreen grid for ONE folder's terminals (subfolders included), opened
// from the folder's ⛶. A true 2D layout: the columns control + drag-order
// compose any shape (3 across with 1 below, 2×2, a stack), and the bars
// between tiles drag to resize columns and rows. Collapsed sessions dock to
// a bottom strip as chips.
export function GridView({
  title,
  agents,
  folders,
  onSwitchFolder,
  onClose,
}: {
  title: string;
  agents: SessionView[];
  folders: string[];
  onSwitchFolder: (folder: string) => void;
  onClose: () => void;
}) {
  const [cols, setCols] = useState<number>(0); // 0 = auto
  const [order, setOrder] = useState<string[]>([]);
  // Default view: vertical sections, at most 3 expanded — sessions beyond the
  // first three start in the dock so the grid opens readable, not cramped.
  const [collapsed, setCollapsed] = useState<Set<string>>(
    () => new Set(agents.slice(3).map((s) => s.key)),
  );
  const [dragKey, setDragKey] = useState<string | null>(null);
  const [colSizes, setColSizes] = useState<number[]>([]);
  const [rowSizes, setRowSizes] = useState<number[]>([]);
  const tilesRef = useRef<HTMLDivElement>(null);

  // Esc exits — but only when focus is OUTSIDE a terminal (bubble phase):
  // inside one, Esc belongs to the agent (denying a claude prompt must not
  // yank the whole grid away). The Exit button always works.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const ordered = useMemo(() => {
    const rank = new Map(order.map((k, i) => [k, i]));
    return [...agents].sort(
      (a, b) => (rank.get(a.key) ?? 999) - (rank.get(b.key) ?? 999),
    );
  }, [agents, order]);
  const tiles = ordered.filter((s) => !collapsed.has(s.key));
  const docked = ordered.filter((s) => collapsed.has(s.key));

  // Auto: one vertical section per session, up to 4 across; more than that
  // wraps into rows. Explicit columns always win.
  const effectiveCols = cols || Math.max(1, Math.min(tiles.length, 4));
  const rowCount = Math.max(1, Math.ceil(tiles.length / effectiveCols));

  // Size arrays follow the shape; user-dragged proportions reset on reshape
  // (a resized 2×2 has no meaningful mapping onto a 3-wide layout).
  useEffect(() => {
    setColSizes(Array(effectiveCols).fill(1));
    setRowSizes(Array(rowCount).fill(1));
  }, [effectiveCols, rowCount]);

  function startResize(
    e: React.PointerEvent,
    axis: "col" | "row",
    index: number,
  ) {
    e.preventDefault();
    const container = tilesRef.current;
    if (!container) return;
    const sizes = axis === "col" ? [...colSizes] : [...rowSizes];
    const total = sizes.reduce((a, b) => a + b, 0);
    const px =
      axis === "col"
        ? container.getBoundingClientRect().width
        : container.getBoundingClientRect().height;
    const start = axis === "col" ? e.clientX : e.clientY;

    const onMove = (ev: PointerEvent) => {
      const delta =
        (((axis === "col" ? ev.clientX : ev.clientY) - start) / px) * total;
      const a = Math.max(0.2, sizes[index] + delta);
      const b = Math.max(0.2, sizes[index + 1] - delta);
      const next = [...sizes];
      next[index] = a;
      next[index + 1] = b;
      (axis === "col" ? setColSizes : setRowSizes)(next);
    };
    const onUp = () => {
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
    };
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
  }

  function moveBefore(target: string) {
    if (!dragKey || dragKey === target) return;
    const keys = ordered.map((v) => v.key).filter((k) => k !== dragKey);
    keys.splice(keys.indexOf(target), 0, dragKey);
    setOrder(keys);
  }

  function toggleCollapse(key: string) {
    setCollapsed((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  }

  return (
    <div className="rd-grid">
      <div className="rd-grid-bar">
        <span className="rd-grid-title">{ordered.length} running</span>
        {/* Switch folders without leaving the grid; the grid remounts per
            folder (keyed in App) so the 3-up default re-applies. */}
        <select
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
        </select>
        <select
          className="rd-grid-folder rd-grid-cols"
          title="Columns — drag tile headers to choose what sits where"
          value={cols}
          onChange={(e) => setCols(Number(e.target.value))}
        >
          <option value={0}>auto columns</option>
          <option value={1}>1 column</option>
          <option value={2}>2 columns</option>
          <option value={3}>3 columns</option>
          <option value={4}>4 columns</option>
        </select>
        <span className="rd-spacer" />
        <button className="rd-btn rd-btn-sm rd-btn-ghost" onClick={onClose}>
          Exit grid (esc)
        </button>
      </div>
      {tiles.length === 0 ? (
        <p className="rd-panel-empty">
          {docked.length > 0
            ? "Every session is collapsed — click a chip below to expand it."
            : "No running terminals in this folder."}
        </p>
      ) : (
        <div
          ref={tilesRef}
          className="rd-grid-tiles"
          style={{
            gridTemplateColumns: colSizes.map((f) => `${f}fr`).join(" "),
            gridTemplateRows: rowSizes.map((f) => `${f}fr`).join(" "),
          }}
        >
          {tiles.map((s, i) => {
            const col = i % effectiveCols;
            const row = Math.floor(i / effectiveCols);
            return (
              <div
                key={s.key}
                className="rd-grid-tile"
                onDragOver={(e) => {
                  if (dragKey) e.preventDefault();
                }}
                onDrop={(e) => {
                  e.preventDefault();
                  moveBefore(s.key);
                  setDragKey(null);
                }}
              >
                <div
                  className="rd-grid-tile-head"
                  draggable
                  title="Drag to rearrange"
                  onDragStart={() => setDragKey(s.key)}
                  onDragEnd={() => setDragKey(null)}
                >
                  <span className={`rd-state st-${s.state}`}>
                    <span className="dot" />
                  </span>
                  <span className="rd-grid-tile-name">{s.label}</span>
                  {s.branch && (
                    <span className="rd-grid-tile-branch">⎇ {s.branch}</span>
                  )}
                  <span className="rd-spacer" />
                  <button
                    className="rd-grid-tile-collapse"
                    title="Collapse to the dock"
                    onClick={() => toggleCollapse(s.key)}
                  >
                    ▾
                  </button>
                </div>
                <div className="rd-grid-tile-term">
                  <Terminal sessionKey={s.key} />
                </div>
                {col < effectiveCols - 1 && (
                  <div
                    className="rd-grid-split-v"
                    title="Drag to resize columns"
                    onPointerDown={(e) => startResize(e, "col", col)}
                  />
                )}
                {row < rowCount - 1 && (
                  <div
                    className="rd-grid-split-h"
                    title="Drag to resize rows"
                    onPointerDown={(e) => startResize(e, "row", row)}
                  />
                )}
              </div>
            );
          })}
        </div>
      )}
      {docked.length > 0 && (
        <div className="rd-grid-dock">
          {docked.map((s) => (
            <button
              key={s.key}
              className="rd-grid-dock-chip"
              title="Bring back into the grid"
              onClick={() => toggleCollapse(s.key)}
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
