import { useEffect, useRef, useState } from "react";
import { type Appearance, DEFAULT_APPEARANCE } from "./appearance";
import "./appearance.css";

function SizeControl({ name, value, min, max, onChange }: { name: string; value: number; min: number; max: number; onChange: (value: number) => void }) {
  return <div className="rd-appearance-field"><div className="rd-appearance-size">
    <button type="button" aria-label={`Smaller ${name} text`} disabled={value <= min} onClick={() => onChange(value - 1)}>−</button>
    <output aria-label={`${name} text size`} aria-live="polite">{value} px</output>
    <button type="button" aria-label={`Larger ${name} text`} disabled={value >= max} onClick={() => onChange(value + 1)}>+</button>
  </div></div>;
}
export function AppearanceSettings({ appearance, update, onClose }: {
  appearance: Appearance; update: (patch: Partial<Appearance>) => void; onClose: () => void;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const closeRef = useRef(onClose); closeRef.current = onClose;
  const [error, setError] = useState("");
  useEffect(() => {
    const previous = document.activeElement;
    const element = dialog.current;
    element?.showModal();
    return () => {
      element?.close();
      const target = previous instanceof HTMLElement && previous !== document.body && previous.isConnected
        ? previous : document.querySelector<HTMLButtonElement>('button[aria-controls="header-settings-panel"]');
      target?.focus();
    };
  }, []);
  const save = (change: Partial<Appearance>) => {
    try { update(change); setError(""); } catch { setError("Could not save appearance settings on this device. Try again."); }
  };
  return <dialog ref={dialog} className="rd-appearance-dialog" aria-labelledby="appearance-title" onCancel={e => { e.preventDefault(); closeRef.current(); }}>
    <header><div><h2 id="appearance-title">Text sizes</h2><p>Adjust the interface and terminal separately.</p></div><button className="rd-btn rd-btn-ghost" aria-label="Close text sizes" onClick={onClose}>✕</button></header>
    <section className="rd-appearance-controls">
      <div className="rd-appearance-row"><div><h3>Interface</h3><p>Session names, messages, menus, and panels.</p></div><SizeControl name="interface" value={appearance.uiSize} min={12} max={20} onChange={uiSize => save({ uiSize })} /></div>
      <div className="rd-appearance-row"><div><h3>Terminal</h3><p>Agent terminals and shell panes.</p></div><SizeControl name="terminal" value={appearance.terminalSize} min={10} max={24} onChange={terminalSize => save({ terminalSize })} /></div>
    </section>
    <footer><button className="rd-btn rd-btn-ghost" onClick={() => save({ uiSize: DEFAULT_APPEARANCE.uiSize, terminalSize: DEFAULT_APPEARANCE.terminalSize })}>Reset text sizes</button><button className="rd-btn rd-btn-primary" onClick={onClose}>Done</button></footer>
    <p className="rd-appearance-note" role={error ? "alert" : undefined}>{error || "Changes apply immediately on this device. Fonts and terminal colors are kept."}</p>
  </dialog>;
}
