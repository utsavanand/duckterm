import { useLayoutEffect, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import "./sessionActionMenu.css";

export type SessionActionAnchor = { key: string; x: number; y: number; trigger: HTMLElement };
export function openSessionActions(anchor: SessionActionAnchor) {
  window.dispatchEvent(new CustomEvent("session-actions", { detail: anchor }));
}

export function SessionActionMenu({ anchor, label, expanded, children, onClose }: {
  anchor: SessionActionAnchor; label: string; expanded: boolean; children: ReactNode; onClose: () => void;
}) {
  const menu = useRef<HTMLDivElement>(null);
  const focused = useRef<HTMLButtonElement | null>(null);
  const close = useRef(onClose); close.current = onClose;
  const [position, setPosition] = useState({ left: anchor.x, top: anchor.y });
  useLayoutEffect(() => {
    if (expanded) return;
    const place = () => {
      const rect = menu.current?.getBoundingClientRect();
      setPosition({ left: Math.max(8, Math.min(anchor.x, window.innerWidth - (rect?.width ?? 248) - 8)),
        top: Math.max(8, Math.min(anchor.y, window.innerHeight - (rect?.height ?? 400) - 8)) });
    };
    const observer = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(place);
    if (menu.current) observer?.observe(menu.current);
    place(); (focused.current && !focused.current.disabled ? focused.current : menu.current)?.focus();
    const outside = (event: PointerEvent) => {
      if (!menu.current?.contains(event.target as Node)) close.current();
    };
    const scroll = (event: Event) => {
      const target = event.target;
      // Terminal output and context-panel refreshes must not dismiss a row menu.
      if (target === document || target === window || (target instanceof Element && target.contains(anchor.trigger))) close.current();
    };
    document.addEventListener("pointerdown", outside);
    window.addEventListener("resize", place);
    window.addEventListener("scroll", scroll, true);
    return () => {
      observer?.disconnect();
      document.removeEventListener("pointerdown", outside);
      window.removeEventListener("resize", place);
      window.removeEventListener("scroll", scroll, true);
    };
  }, [anchor, expanded]);
  return createPortal(<div ref={menu} role="menu" aria-label={`Actions for ${label}`} tabIndex={-1}
    className="rd-session-menu" style={{ ...position, visibility: expanded ? "hidden" : undefined }}
    onFocusCapture={e => { if (e.target instanceof HTMLButtonElement && menu.current?.contains(e.target)) focused.current = e.target; }}
    onContextMenu={e => e.preventDefault()} onKeyDown={e => {
      if (!e.currentTarget.contains(e.target as Node)) return;
      const items = [...e.currentTarget.querySelectorAll<HTMLButtonElement>('button[role="menuitem"]:not(:disabled)')];
      const index = items.indexOf(document.activeElement as HTMLButtonElement);
      if (e.key === "Escape" || e.key === "Tab") { e.preventDefault(); e.stopPropagation(); onClose(); }
      else if (["ArrowDown", "ArrowUp", "Home", "End"].includes(e.key)) {
        e.preventDefault(); e.stopPropagation();
        const next = e.key === "Home" ? 0 : e.key === "End" ? items.length - 1 :
          e.key === "ArrowDown" ? (index + 1) % items.length : (index <= 0 ? items.length : index) - 1;
        items[next]?.focus();
      }
    }}>{children}</div>, document.body);
}
