import { useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import type { ModelChoice } from "./api";

export function ModelMenu({ anchor, choices, current, loading, error, retry, select, close }: {
  anchor: HTMLButtonElement; choices: ModelChoice[]; current: string; loading: boolean;
  error: string; retry: () => void; select: (model: string) => void; close: () => void;
}) {
  const menu = useRef<HTMLDivElement>(null);
  const [position, setPosition] = useState({ left: 8, top: 8, width: 320, maxHeight: 320 });
  useLayoutEffect(() => {
    const place = () => {
      const rect = anchor.getBoundingClientRect();
      const width = Math.min(360, window.innerWidth - 16);
      const below = window.innerHeight - rect.bottom - 16;
      const above = rect.top - 16;
      const maxHeight = Math.max(40, Math.min(340, Math.max(below, above)));
      const height = Math.min(menu.current?.scrollHeight ?? 340, maxHeight);
      setPosition({ left: Math.max(8, Math.min(rect.right - width, window.innerWidth - width - 8)),
        top: below >= height ? rect.bottom + 6 : Math.max(8, rect.top - height - 6), width, maxHeight });
    };
    place();
    window.addEventListener("resize", place);
    window.addEventListener("scroll", place, true);
    const outside = (e: PointerEvent) => {
      if (!menu.current?.contains(e.target as Node) && !anchor.contains(e.target as Node)) close();
    };
    document.addEventListener("pointerdown", outside);
    menu.current?.focus();
    return () => {
      window.removeEventListener("resize", place);
      window.removeEventListener("scroll", place, true);
      document.removeEventListener("pointerdown", outside);
    };
  }, [anchor, choices, loading, error, close]);
  return createPortal(<div ref={menu} className="rd-model-menu" role="menu" aria-label="Choose model" tabIndex={-1} style={position} onKeyDown={e => {
    const options = [...e.currentTarget.querySelectorAll<HTMLButtonElement>("button:not(:disabled)")];
    const index = options.indexOf(document.activeElement as HTMLButtonElement);
    if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); close(); }
    else if (e.key === "Tab") { close(); }
    else if (["ArrowDown", "ArrowUp", "Home", "End"].includes(e.key)) {
      e.preventDefault();
      const next = e.key === "Home" ? 0 : e.key === "End" ? options.length - 1 :
        e.key === "ArrowDown" ? (index + 1) % options.length : (index <= 0 ? options.length : index) - 1;
      options[next]?.focus();
    }
  }}>
    {loading && <p role="status">Loading available models…</p>}
    {error && <div><p role="alert">{error}</p><button role="menuitem" onClick={retry}>Retry model list</button></div>}
    {!loading && !error && choices.map(choice => <button key={choice.id} role="menuitemradio" aria-checked={choice.id === current} onClick={() => select(choice.id)}>
      <span>{choice.label}{choice.id === current && <small>Current</small>}</span><code>{choice.id}</code>
    </button>)}
  </div>, document.body);
}
