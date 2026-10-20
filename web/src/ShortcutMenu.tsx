import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { SHORTCUT_COLORS, type ShortcutColor } from "./shortcutPreferences";

export type ShortcutMenuTarget = { key: string; anchor: HTMLButtonElement };
export function ShortcutMenu({ target, color, onColor, onClose }: {
  target: ShortcutMenuTarget; color: ShortcutColor;
  onColor: (color: ShortcutColor) => void; onClose: () => void;
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
  }, [target]);
  useEffect(() => {
    (root.current?.querySelector<HTMLButtonElement>('button[aria-pressed="true"]') ?? root.current?.querySelector("button"))?.focus();
    const outside = (event: PointerEvent) => {
      if (event.target instanceof Node && !root.current?.contains(event.target) && !target.anchor.contains(event.target)) close.current();
    };
    const escape = (event: KeyboardEvent) => { if (event.key === "Escape") { event.preventDefault(); event.stopPropagation(); close.current(); } };
    document.addEventListener("pointerdown", outside); document.addEventListener("keydown", escape, true);
    return () => { document.removeEventListener("pointerdown", outside); document.removeEventListener("keydown", escape, true); };
  }, [target]);
  return createPortal(<div ref={root} className="rd-shortcut-menu" role="dialog" aria-label="Shortcut color" style={position}
    onBlur={event => { if (event.relatedTarget instanceof Node && !event.currentTarget.contains(event.relatedTarget) && !target.anchor.contains(event.relatedTarget)) onClose(); }}>
    <header><strong>Shortcut color</strong><button aria-label="Close shortcut controls" onClick={onClose}>×</button></header>
    <p>Personal label color · status stays separate</p>
    <div className="rd-shortcut-swatches">{Object.entries(SHORTCUT_COLORS).map(([name, hex]) => <button key={name} title={name} aria-label={name} aria-pressed={color === name}
      onClick={() => { onColor(name as ShortcutColor); onClose(); }}>{hex ? <i aria-hidden="true" style={{ background: hex }} /> : "None"}</button>)}</div>
  </div>, document.body);
}
