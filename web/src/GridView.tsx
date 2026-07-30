import { useEffect, useMemo, useState } from "react";
import { Terminal } from "./Terminal";
import { SessionView } from "./types";

// Fullscreen grid: every PTY-owned session tiled in a true 2D layout. The
// columns control + drag-to-rearrange compose any shape — 3 across with one
// below, 2×2, a single stack. Collapsed sessions dock to a bottom strip as
// chips (click to bring back), so the grid itself only holds live tiles.
export function GridView({
  agents,
  folders,
  onClose,
}: {
  agents: SessionView[];
  folders: string[];
  onClose: () => void;
}) {
  const [cols, setCols] = useState<number>(0); // 0 = auto
  const [folder, setFolder] = useState<string>("");
  const [order, setOrder] = useState<string[]>([]);
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());
  const [dragKey, setDragKey] = useState<string | null>(null);

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

  const inFolder = useMemo(
    () => (folder ? agents.filter((a) => a.group === folder) : agents),
    [agents, folder],
  );
  const ordered = useMemo(() => {
    const rank = new Map(order.map((k, i) => [k, i]));
    return [...inFolder].sort(
      (a, b) => (rank.get(a.key) ?? 999) - (rank.get(b.key) ?? 999),
    );
  }, [inFolder, order]);
  const tiles = ordered.filter((s) => !collapsed.has(s.key));
  const docked = ordered.filter((s) => collapsed.has(s.key));

  // Auto: the squarest grid that fits (1→1, 2→2, 3→3 across, 4→2×2, 5-6→3…).
  const effectiveCols =
    cols || Math.min(tiles.length, Math.ceil(Math.sqrt(tiles.length)) + 1, 3);

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
        <span className="rd-grid-title">
          {folder || "All agents"} · {ordered.length}
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
        <select
          className="rd-grid-folder"
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
      {tiles.length === 0 && docked.length === 0 ? (
        <p className="rd-panel-empty">No running terminals in this folder.</p>
      ) : (
        <div
          className="rd-grid-tiles"
          style={{ gridTemplateColumns: `repeat(${effectiveCols}, 1fr)` }}
        >
          {tiles.map((s) => (
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
            </div>
          ))}
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
