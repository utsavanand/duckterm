import { useEffect, useRef, useState, type CSSProperties } from "react";
import { type Appearance, DEFAULT_APPEARANCE, TERMINAL_FONTS, UI_FONTS } from "./appearance";
import { AUTO, DEFAULT_BY_MODE, TERM_THEMES, themesForMode, type TermMode } from "./termThemes";
import type { Theme } from "./useTheme";
import "./appearance.css";

const themeNames: Record<string, string> = { duck: "Duck", dracula: "Dracula", nord: "Nord", "gruvbox-dark": "Gruvbox Dark", "solarized-dark": "Solarized Dark", paper: "Paper", "solarized-light": "Solarized Light" };
function SizeControl({ name, value, min, max, onChange }: { name: string; value: number; min: number; max: number; onChange: (value: number) => void }) {
  return <div className="rd-appearance-field"><span>Text size</span><div className="rd-appearance-size">
    <button type="button" aria-label={`Smaller ${name} text`} disabled={value <= min} onClick={() => onChange(value - 1)}>−</button>
    <output aria-label={`${name} text size`} aria-live="polite">{value} px</output>
    <button type="button" aria-label={`Larger ${name} text`} disabled={value >= max} onClick={() => onChange(value + 1)}>+</button>
  </div></div>;
}
export function AppearanceSettings({ appearance, update, theme, mode, onTheme, termTheme, onTermTheme, onResetColors, onClose }: {
  appearance: Appearance; update: (patch: Partial<Appearance>) => void; theme: Theme; mode: TermMode; onTheme: (theme: Theme) => void; termTheme: string; onTermTheme: (name: string) => void; onResetColors: () => void; onClose: () => void;
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
  const paletteName = termTheme !== AUTO && TERM_THEMES[termTheme]?.mode === mode ? termTheme : DEFAULT_BY_MODE[mode];
  const palette = TERM_THEMES[paletteName];
  const terminalStyle = { fontFamily: TERMINAL_FONTS[appearance.terminalFont].css, fontSize: appearance.terminalSize, background: palette.background, color: palette.foreground, "--preview-green": palette.green, "--preview-cyan": palette.cyan, "--preview-yellow": palette.yellow, "--preview-red": palette.red, "--preview-blue": palette.blue } as CSSProperties;
  return <dialog ref={dialog} className="rd-appearance-dialog" aria-labelledby="appearance-title" onCancel={e => { e.preventDefault(); closeRef.current(); }}>
    <header><div><h2 id="appearance-title">Appearance</h2><p>Adjust text and colors to make your workspace easier to read.</p></div><button className="rd-btn rd-btn-ghost" aria-label="Close Appearance" onClick={onClose}>✕</button></header>
    <div className="rd-appearance-layout"><div className="rd-appearance-controls">
      <section aria-labelledby="appearance-interface"><h3 id="appearance-interface">Interface</h3>
        <label className="rd-appearance-field">Theme<select value={theme} onChange={e => onTheme(e.target.value as Theme)}><option value="system">Follow system</option><option value="light">Light</option><option value="dark">Dark</option></select></label>
        <label className="rd-appearance-field">Font<select aria-label="Interface font" value={appearance.uiFont} onChange={e => save({ uiFont: e.target.value as Appearance["uiFont"] })}>{Object.entries(UI_FONTS).map(([id, font]) => <option key={id} value={id}>{font.name}</option>)}</select></label>
        <SizeControl name="interface" value={appearance.uiSize} min={12} max={20} onChange={uiSize => save({ uiSize })} />
        <p>For session names, messages, menus, and the rest of DuckTerm. Terminal text has its own settings below.</p>
      </section>
      <section aria-labelledby="appearance-terminal"><h3 id="appearance-terminal">Terminal</h3>
        <label className="rd-appearance-field">Font<select aria-label="Terminal font" value={appearance.terminalFont} onChange={e => save({ terminalFont: e.target.value as Appearance["terminalFont"] })}>{Object.entries(TERMINAL_FONTS).map(([id, font]) => <option key={id} value={id}>{font.name}</option>)}</select></label>
        <SizeControl name="terminal" value={appearance.terminalSize} min={10} max={24} onChange={terminalSize => save({ terminalSize })} />
        <label className="rd-appearance-field">Color theme<select className="rd-term-theme" value={termTheme} onChange={e => onTermTheme(e.target.value)}><option value={AUTO}>Follow app theme</option>{themesForMode(mode).map(name => <option key={name} value={name}>{themeNames[name] ?? name}</option>)}</select></label>
        <p>Default for terminals and shells. Any folder or session theme you’ve chosen still takes priority.</p>
      </section>
    </div><div className="rd-appearance-preview">
      <div className="rd-appearance-preview-label"><span>Live preview</span><span>{themeNames[paletteName]} · {mode === "dark" ? "Dark" : "Light"}</span></div>
      <div className="rd-appearance-sample"><div className="rd-appearance-sample-head"><strong>Workspace</strong><span>New session</span></div>
        <div className="rd-appearance-sample-body"><div className="rd-appearance-sessions"><strong>Website</strong><div className="selected">🦆 design <small>Working</small></div><div>🦆 review</div><div>🦆 docs</div></div>
        <pre className="rd-appearance-terminal" style={terminalStyle}><span className="cmd">❯ npm run test</span>{"\n\n"}<span className="ok">✓</span>{" Navigation stays readable\n"}<span className="ok">✓</span>{" Session switching works\n"}<span className="ok">✓</span>{" Changes are saved\n\n"}<span className="info">3 tests passed</span>{"\n"}<span className="warn">Review one changed file</span>{"\n"}<span className="err">− old heading</span>{"\n"}<span className="ok">+ clearer heading</span>{"\n\nReady for the next task.\n"}<span className="cmd">❯ ▍</span></pre></div>
        <div className="rd-appearance-message"><small>Messages · Latest reply</small><p><strong>The update is ready.</strong> Text remains readable as you increase the interface size. Terminal text is adjusted independently.</p></div>
      </div><p className="rd-appearance-note">Colors shown here demonstrate a program’s colored output. Plain-text output stays plain; choosing a palette doesn’t add syntax highlighting to it.</p>
    </div></div>
    <footer><div><button className="rd-btn rd-btn-ghost" onClick={() => { save(DEFAULT_APPEARANCE); onTheme("system"); onResetColors(); }}>Reset to defaults</button><p role={error ? "alert" : undefined}>{error || "Changes apply immediately and are saved on this device."}</p></div><button className="rd-btn rd-btn-primary" onClick={onClose}>Done</button></footer>
  </dialog>;
}
