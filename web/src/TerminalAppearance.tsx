import { useState } from "react";
import { TERM_THEMES, themesForMode, type TermMode } from "./termThemes";
import { useTerminalContrast, type TextContrast } from "./terminalContrast";
import "./terminalAppearance.css";

const names: Record<string, string> = { duck: "Duck", dracula: "Dracula", nord: "Nord", "gruvbox-dark": "Gruvbox Dark", "solarized-dark": "Solarized Dark", paper: "Paper", "solarized-light": "Solarized Light" };
export function TerminalAppearance({ scope, target, mode, theme, effectiveTheme, onTheme, overrideCount = 0 }: {
  scope: "sessions" | "folders"; target: string; mode: TermMode; theme?: string; effectiveTheme: string; onTheme: (theme: string | null) => void; overrideCount?: number;
}) {
  const { preferences, update } = useTerminalContrast();
  const [error, setError] = useState("");
  const folder = scope === "folders";
  const pick = theme && TERM_THEMES[theme]?.mode === mode ? theme : "";
  const contrast = preferences[scope][target] ?? "";
  return <div className="rd-terminal-appearance">
    {folder && <p>Default for this folder and its subfolders. Sessions with their own theme keep it.</p>}
    <label>{folder ? "Folder theme" : "Theme for this session"}<select value={pick} onChange={e => onTheme(e.target.value || null)}>
      <option value="">{folder ? "Use inherited default" : "Use folder setting"}</option>
      {themesForMode(mode).map(name => <option key={name} value={name}>{names[name]}</option>)}
    </select></label>
    <label>Text contrast<select value={contrast} onChange={e => {
      try { update(scope, target, (e.target.value || null) as TextContrast | null); setError(""); }
      catch { setError("Could not save text contrast on this device. Try again."); }
    }}>
      <option value="">{folder ? "Use inherited setting" : "Use folder setting"}</option><option value="readable">Improve readability</option><option value="preserve">Keep program colors</option>
    </select></label>
    {error && <p role="alert">{error}</p>}
    <div className="rd-terminal-appearance-effective">Using <strong>{names[effectiveTheme] ?? effectiveTheme}</strong> · {pick ? folder ? "Folder default" : "Session override" : "Inherited default"}</div>
    {folder ? <><p>Improved readability applies the theme to all 256 terminal colors and adjusts low-contrast text. Keep program colors preserves the extended palette.</p><p>{overrideCount} {overrideCount === 1 ? "session has" : "sessions have"} a theme override.</p><p>Saved on this device. Existing terminals update without restarting agents.</p></>
      : <p>A session override stays in place when its folder theme changes. Choose “Use folder setting” to inherit again.</p>}
    <details><summary>How program colors behave</summary><p>The theme changes default text and all 256 terminal colors, including the extended colors used by agents. Explicit RGB colors remain program-controlled, with low-contrast text adjusted for readability. “Keep program colors” preserves extended and RGB colors. Inherited contrast defaults to improved readability.</p></details>
  </div>;
}
