import { useEffect, useMemo, useState } from "react";
import { Terminal } from "./Terminal";
import { SessionView } from "./types";

// Fullscreen grid: every PTY-owned session in one view, tiled side by side
// (columns) or stacked (rows). Tiles drag-reorder; a tile collapses to its
// header bar. This is the "watch the whole fleet work" mode — the three-pane
// layout unmounts underneath, so each session keeps exactly one terminal WS.
export function GridView({
  agents,
  folders,
  onClose,
}: {
  agents: SessionView[];
  folders: string[];
  onClose: () => void;
}) {
  const [orientation, setOrientation] = useState<"cols" | "rows">("cols");
  const [folder, setFolder] = useState<string>("");
  const [order, setOrder] = useState<string[]>([]);
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());
  const [dragKey, setDragKey] = useState<string | null>(null);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const visible = useMemo(() => {
    const inFolder = folder ? agents.filter((a) => a.group === folder) : agents;
    // Stable manual order first, new arrivals appended.
    const rank = new Map(order.map((k, i) => [k, i]));
    return [...inFolder].sort(
      (a, b) => (rank.get(a.key) ?? 999) - (rank.get(b.key) ?? 999),
    );
  }, [agents, folder, order]);

  function moveBefore(target: string) {
    if (!dragKey || dragKey === target) return;
    const keys = visible.map((v) => v.key).filter((k) => k !== dragKey);
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
        <span className="rd-grid-title">
          {folder || "All agents"} · {visible.length}
        </span>
        {folders.length > 0 && (
          <select
            className="rd-grid-folder"
            value={folder}
            onChange={(e) => setFolder(e.target.value)}
          >
            <option value="">All agents</option>
            {folders.map((f) => (
              <option key={f} value={f}>
                {f}
              </option>
            ))}
          </select>
        )}
        <button
          className="rd-btn rd-btn-sm rd-btn-ghost"
          title="Arrange side by side or stacked"
          onClick={() =>
            setOrientation((o) => (o === "cols" ? "rows" : "cols"))
          }
        >
          {orientation === "cols" ? "⬌ side by side" : "⬍ stacked"}
        </button>
        <span className="rd-spacer" />
        <button className="rd-btn rd-btn-sm rd-btn-ghost" onClick={onClose}>
          Exit grid (esc)
        </button>
      </div>
      {visible.length === 0 ? (
        <p className="rd-panel-empty">No running terminals in this folder.</p>
      ) : (
        <div className={`rd-grid-tiles ${orientation}`}>
          {visible.map((s) => {
            const isCollapsed = collapsed.has(s.key);
            return (
              <div
                key={s.key}
                className={`rd-grid-tile${isCollapsed ? " collapsed" : ""}`}
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
                    title={isCollapsed ? "Expand" : "Collapse to a bar"}
                    onClick={() => toggleCollapse(s.key)}
                  >
                    {isCollapsed ? "▸" : "▾"}
                  </button>
                </div>
                {/* Keep the terminal mounted when collapsed (hidden, WS held)
                    so expanding repaints instantly via the resize observer. */}
                <div
                  className="rd-grid-tile-term"
                  style={{ display: isCollapsed ? "none" : "flex" }}
                >
                  <Terminal sessionKey={s.key} />
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
