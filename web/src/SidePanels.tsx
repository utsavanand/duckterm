import { useEffect, useState } from "react";
import "./sidePanels.css";

export function useSidePanels() {
  const [collapsed, setCollapsed] = useState(() => {
    try {
      const saved = JSON.parse(localStorage.getItem("rd.panelsCollapsed") || "{}");
      return { left: saved?.left === true, right: saved?.right === true };
    } catch { return { left: false, right: false }; }
  });
  useEffect(() => {
    try { localStorage.setItem("rd.panelsCollapsed", JSON.stringify(collapsed)); }
    catch { /* Panel controls still work when storage is unavailable. */ }
  }, [collapsed]);
  return { collapsed, toggle: (side: "left" | "right") => setCollapsed(current => ({ ...current, [side]: !current[side] })) };
}

export function PanelToggle({ side, collapsed, onToggle }: { side: "left" | "right"; collapsed: boolean; onToggle: () => void }) {
  const label = `${collapsed ? "Show" : "Collapse"} ${side === "left" ? "Agents" : "Context"} panel`;
  const pointsLeft = side === "left" ? !collapsed : collapsed;
  return <button className="rd-panel-toggle" type="button" title={label} aria-label={label} aria-expanded={!collapsed} onClick={onToggle}>
    <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden="true">
      <rect x="2" y="2" width="12" height="12" rx="2" />
      <path d={`M${pointsLeft ? 6 : 10} 2v12`} />
      <path d={pointsLeft ? "M10 6l-2 2 2 2" : "M6 6l2 2-2 2"} />
    </svg>
  </button>;
}
