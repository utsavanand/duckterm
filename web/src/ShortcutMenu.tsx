import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { SHORTCUT_COLORS, type ShortcutColor } from "./shortcutPreferences";

export type ShortcutMenuTarget = { key: string; mode: "color" | "move"; anchor: HTMLButtonElement };
export function ShortcutMenu({ target, label, color, canUp, canDown, onColor, onMove, onClose }: {
  target: ShortcutMenuTarget; label: string; color: ShortcutColor; canUp: boolean; canDown: boolean;
  onColor: (color: ShortcutColor) => void; onMove: (direction: -1 | 1) => void; onClose: () => void;
}) {
  const root = useRef<HTMLDivElement>(null);
  const close = useRef(onClose); close.current = onClose;
  const [position, setPosition] = useState({ left: 8, top: 8 });
  useLayoutEffect(() => {
    const place = () => {
      const r = target.anchor.getBoundingClientRect();
      const height = root.current?.offsetHeight ?? 250;
      setPosition({ left: Math.max(8, Math.min(r.right - 244, window.innerWidth - 252)), top: Math.max(8, Math.min(r.bottom + 5, window.innerHeight - height - 8)) });
    };
    place(); window.addEventListener("resize", place); window.addEventListener("scroll", place, true);
    return () => { window.removeEventListener("resize", place); window.removeEventListener("scroll", place, true); };
  }, [target, label, canUp, canDown]);
  useEffect(() => {
    (root.current?.querySelector<HTMLButtonElement>('button[aria-pressed="true"]') ?? root.current?.querySelector<HTMLButtonElement>(".rd-shortcut-menu-actions button:not(:disabled)") ?? root.current?.querySelector("button"))?.focus();
    const outside = (event: PointerEvent) => {
      if (event.target instanceof Node && !root.current?.contains(event.target) && !target.anchor.contains(event.target)) close.current();
    };
    const escape = (event: KeyboardEvent) => { if (event.key === "Escape") { event.preventDefault(); event.stopPropagation(); close.current(); } };
    document.addEventListener("pointerdown", outside); document.addEventListener("keydown", escape, true);
    return () => { document.removeEventListener("pointerdown", outside); document.removeEventListener("keydown", escape, true); };
  }, [target]);
  useEffect(() => {
    if (root.current?.contains(document.activeElement) && document.activeElement instanceof HTMLButtonElement && document.activeElement.disabled) {
      root.current.querySelector<HTMLButtonElement>(".rd-shortcut-menu-actions button:not(:disabled)")?.focus();
    }
  }, [canUp, canDown]);
  return createPortal(<div ref={root} className="rd-shortcut-menu" role="dialog" aria-label={target.mode === "color" ? "Shortcut color" : "Move shortcut"} style={position}
    onBlur={event => { if (event.relatedTarget instanceof Node && !event.currentTarget.contains(event.relatedTarget) && !target.anchor.contains(event.relatedTarget)) onClose(); }}>
    <header><strong>{target.mode === "color" ? "Shortcut color" : "Move shortcut"}</strong><button aria-label="Close shortcut controls" onClick={onClose}>×</button></header>
    <p>{target.mode === "color" ? "Personal label color · status stays separate" : label}</p>
    {target.mode === "color" ? <div className="rd-shortcut-swatches">{Object.entries(SHORTCUT_COLORS).map(([name, hex]) => <button key={name} title={name} aria-label={name} aria-pressed={color === name}
      onClick={() => { onColor(name as ShortcutColor); onClose(); }}>{hex ? <i aria-hidden="true" style={{ background: hex }} /> : "None"}</button>)}</div>
      : <><div className="rd-shortcut-menu-actions"><button disabled={!canUp} onClick={event => { onMove(-1); event.currentTarget.focus(); }}>↑ Move up</button>
        <button disabled={!canDown} onClick={event => { onMove(1); event.currentTarget.focus(); }}>↓ Move down</button></div><p>Or focus the grip and press Alt + ↑ / ↓.</p></>}
  </div>, document.body);
}
