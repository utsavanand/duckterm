import { useEffect, useRef, useState, type PointerEvent as ReactPointerEvent, type DragEvent, type RefObject } from "react";

type Target = { key: string; after: boolean };
type Gesture = { key: string; id: number; x: number; y: number; active: boolean; source: HTMLButtonElement; target: Target | null };

// Pointer capture works for the whole button in native WebKit, including collapsed
// initials. HTML drag-and-drop only on a small grip did not match how owners drag tabs.
export function useShortcutDrag(root: RefObject<HTMLElement>, onDrop: (key: string, target: string, after: boolean) => void, onStart: () => void) {
  const gesture = useRef<Gesture | null>(null);
  const suppressed = useRef<string | null>(null);
  const [dragKey, setDragKey] = useState<string | null>(null);
  const [drop, setDrop] = useState<Target | null>(null);
  function cancel() {
    const current = gesture.current;
    gesture.current = null;
    if (current?.active) suppressed.current = current.key;
    if (current?.source.hasPointerCapture(current.id)) current.source.releasePointerCapture(current.id);
    setDragKey(null); setDrop(null);
  }
  useEffect(() => {
    const escape = (event: KeyboardEvent) => {
      if (event.key !== "Escape" || !gesture.current) return;
      event.preventDefault(); event.stopPropagation(); cancel();
    };
    const blur = () => cancel();
    document.addEventListener("keydown", escape, true); window.addEventListener("blur", blur);
    return () => { document.removeEventListener("keydown", escape, true); window.removeEventListener("blur", blur); };
  }, []);
  function bindings(key: string) {
    return {
      draggable: false,
      onDragStart: (event: DragEvent<HTMLButtonElement>) => event.preventDefault(),
      onPointerDown: (event: ReactPointerEvent<HTMLButtonElement>) => {
        if (event.button !== 0 || event.isPrimary === false) return;
        suppressed.current = null;
        gesture.current = { key, id: event.pointerId, x: event.clientX, y: event.clientY, active: false, source: event.currentTarget, target: null };
        event.currentTarget.setPointerCapture(event.pointerId);
      },
      onPointerMove: (event: ReactPointerEvent<HTMLButtonElement>) => {
        const current = gesture.current;
        if (!current || current.id !== event.pointerId) return;
        if (!current.active && Math.hypot(event.clientX - current.x, event.clientY - current.y) < 5) return;
        event.preventDefault();
        if (!current.active) { current.active = true; setDragKey(key); onStart(); }
        const list = root.current?.querySelector<HTMLElement>(".rd-shortcut-list");
        if (!list) return;
        const bounds = list.getBoundingClientRect();
        let target: Target | null = null;
        if (event.clientX >= bounds.left && event.clientX <= bounds.right && event.clientY >= bounds.top && event.clientY <= bounds.bottom) {
          if (event.clientY < bounds.top + 18) list.scrollTop -= 12;
          else if (event.clientY > bounds.bottom - 18) list.scrollTop += 12;
          const rows = [...list.querySelectorAll<HTMLElement>(".rd-shortcut-tab")];
          const row = rows.find(item => event.clientY <= item.getBoundingClientRect().bottom) ?? rows.at(-1);
          if (row?.dataset.key && row.dataset.key !== key) {
            const rect = row.getBoundingClientRect();
            target = { key: row.dataset.key, after: event.clientY >= rect.top + rect.height / 2 };
          }
        }
        current.target = target; setDrop(target);
      },
      onPointerUp: (event: ReactPointerEvent<HTMLButtonElement>) => {
        const current = gesture.current;
        if (!current || current.id !== event.pointerId) return;
        if (current.active) { event.preventDefault(); event.stopPropagation(); }
        cancel();
        if (current.active && current.target) onDrop(current.key, current.target.key, current.target.after);
      },
      onPointerCancel: (event: ReactPointerEvent<HTMLButtonElement>) => { if (gesture.current?.id === event.pointerId) cancel(); },
      onLostPointerCapture: (event: ReactPointerEvent<HTMLButtonElement>) => { if (gesture.current?.id === event.pointerId) cancel(); },
    };
  }
  function consumeClick(key: string, detail: number) {
    if (detail === 0 || suppressed.current !== key) return false;
    suppressed.current = null; return true;
  }
  return { bindings, consumeClick, dragKey, drop };
}
