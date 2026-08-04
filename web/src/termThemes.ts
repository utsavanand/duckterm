// Terminal color themes (xterm.js ITheme palettes). Full 16-color ANSI sets —
// TUIs render wrong without them. Each theme is tagged light/dark so the
// terminal can follow the app's light/dark toggle: the app mode chooses the
// default and constrains the picker to themes of that mode.

export type TermMode = "light" | "dark";

export interface TermTheme {
  mode: TermMode;
  background: string;
  foreground: string;
  cursor: string;
  black: string;
  red: string;
  green: string;
  yellow: string;
  blue: string;
  magenta: string;
  cyan: string;
  white: string;
  brightBlack: string;
  brightRed: string;
  brightGreen: string;
  brightYellow: string;
  brightBlue: string;
  brightMagenta: string;
  brightCyan: string;
  brightWhite: string;
}

export const TERM_THEMES: Record<string, TermTheme> = {
  duck: {
    mode: "dark",
    background: "#0c0f16",
    foreground: "#d1d5db",
    cursor: "#fbbf24",
    black: "#1f2430",
    red: "#f87171",
    green: "#4ade80",
    yellow: "#fbbf24",
    blue: "#60a5fa",
    magenta: "#c084fc",
    cyan: "#22d3ee",
    white: "#d1d5db",
    brightBlack: "#4b5563",
    brightRed: "#fca5a5",
    brightGreen: "#86efac",
    brightYellow: "#fde68a",
    brightBlue: "#93c5fd",
    brightMagenta: "#d8b4fe",
    brightCyan: "#67e8f9",
    brightWhite: "#f9fafb",
  },
  dracula: {
    mode: "dark",
    background: "#282a36",
    foreground: "#f8f8f2",
    cursor: "#f8f8f2",
    black: "#21222c",
    red: "#ff5555",
    green: "#50fa7b",
    yellow: "#f1fa8c",
    blue: "#bd93f9",
    magenta: "#ff79c6",
    cyan: "#8be9fd",
    white: "#f8f8f2",
    brightBlack: "#6272a4",
    brightRed: "#ff6e6e",
    brightGreen: "#69ff94",
    brightYellow: "#ffffa5",
    brightBlue: "#d6acff",
    brightMagenta: "#ff92df",
    brightCyan: "#a4ffff",
    brightWhite: "#ffffff",
  },
  "solarized-dark": {
    mode: "dark",
    background: "#002b36",
    foreground: "#839496",
    cursor: "#93a1a1",
    black: "#073642",
    red: "#dc322f",
    green: "#859900",
    yellow: "#b58900",
    blue: "#268bd2",
    magenta: "#d33682",
    cyan: "#2aa198",
    white: "#eee8d5",
    brightBlack: "#586e75",
    brightRed: "#cb4b16",
    brightGreen: "#586e75",
    brightYellow: "#657b83",
    brightBlue: "#839496",
    brightMagenta: "#6c71c4",
    brightCyan: "#93a1a1",
    brightWhite: "#fdf6e3",
  },
  "gruvbox-dark": {
    mode: "dark",
    background: "#282828",
    foreground: "#ebdbb2",
    cursor: "#ebdbb2",
    black: "#282828",
    red: "#cc241d",
    green: "#98971a",
    yellow: "#d79921",
    blue: "#458588",
    magenta: "#b16286",
    cyan: "#689d6a",
    white: "#a89984",
    brightBlack: "#928374",
    brightRed: "#fb4934",
    brightGreen: "#b8bb26",
    brightYellow: "#fabd2f",
    brightBlue: "#83a598",
    brightMagenta: "#d3869b",
    brightCyan: "#8ec07c",
    brightWhite: "#ebdbb2",
  },
  nord: {
    mode: "dark",
    background: "#2e3440",
    foreground: "#d8dee9",
    cursor: "#d8dee9",
    black: "#3b4252",
    red: "#bf616a",
    green: "#a3be8c",
    yellow: "#ebcb8b",
    blue: "#81a1c1",
    magenta: "#b48ead",
    cyan: "#88c0d0",
    white: "#e5e9f0",
    brightBlack: "#4c566a",
    brightRed: "#bf616a",
    brightGreen: "#a3be8c",
    brightYellow: "#ebcb8b",
    brightBlue: "#81a1c1",
    brightMagenta: "#b48ead",
    brightCyan: "#8fbcbb",
    brightWhite: "#eceff4",
  },
  paper: {
    mode: "light",
    background: "#ffffff",
    foreground: "#1f2328",
    cursor: "#c48f00",
    black: "#24292f",
    red: "#cf222e",
    green: "#116329",
    yellow: "#953800",
    blue: "#0969da",
    magenta: "#8250df",
    cyan: "#1b7c83",
    white: "#6e7781",
    brightBlack: "#57606a",
    brightRed: "#a40e26",
    brightGreen: "#1a7f37",
    brightYellow: "#633c01",
    brightBlue: "#218bff",
    brightMagenta: "#a475f9",
    brightCyan: "#3192aa",
    brightWhite: "#8c959f",
  },
  "solarized-light": {
    mode: "light",
    background: "#fdf6e3",
    foreground: "#657b83",
    cursor: "#586e75",
    black: "#073642",
    red: "#dc322f",
    green: "#859900",
    yellow: "#b58900",
    blue: "#268bd2",
    magenta: "#d33682",
    cyan: "#2aa198",
    white: "#eee8d5",
    brightBlack: "#002b36",
    brightRed: "#cb4b16",
    brightGreen: "#586e75",
    brightYellow: "#657b83",
    brightBlue: "#839496",
    brightMagenta: "#6c71c4",
    brightCyan: "#93a1a1",
    brightWhite: "#fdf6e3",
  },
};

// The terminal follows the app's light/dark toggle: each mode has its own
// default and its own remembered pick. "auto" (the default global pick) means
// "just use the mode's default"; picking a specific theme only applies while
// the app is in that theme's mode.
export const DEFAULT_BY_MODE: Record<TermMode, string> = {
  dark: "duck",
  light: "paper",
};
// Last-resort fallback for a bare Terminal mount before a mode is resolved.
export const DEFAULT_TERM_THEME = DEFAULT_BY_MODE.dark;
export const AUTO = "auto";

export function themesForMode(mode: TermMode): string[] {
  return Object.keys(TERM_THEMES).filter((k) => TERM_THEMES[k].mode === mode);
}

// The global pick is stored per mode ({dark, light}), so switching the app
// toggle restores whatever you'd chosen for that side rather than carrying a
// dark theme into light mode.
export function loadTermThemes(): Record<TermMode, string> {
  try {
    const raw = JSON.parse(localStorage.getItem("rd-term-theme") ?? "{}");
    const pick = (m: TermMode) =>
      typeof raw[m] === "string" && (raw[m] === AUTO || raw[m] in TERM_THEMES)
        ? raw[m]
        : AUTO;
    return { dark: pick("dark"), light: pick("light") };
  } catch {
    return { dark: AUTO, light: AUTO };
  }
}

export function saveTermThemes(picks: Record<TermMode, string>): void {
  localStorage.setItem("rd-term-theme", JSON.stringify(picks));
}

// Overrides let one session (or a whole folder subtree) render differently.
// Stored client-side like the global choice — it's a per-viewer display pref.
export interface ThemeOverrides {
  sessions: Record<string, string>;
  folders: Record<string, string>;
}

const OVERRIDES_KEY = "rd-term-theme-overrides";

export function loadThemeOverrides(): ThemeOverrides {
  try {
    const raw = JSON.parse(localStorage.getItem(OVERRIDES_KEY) ?? "{}");
    return { sessions: raw.sessions ?? {}, folders: raw.folders ?? {} };
  } catch {
    return { sessions: {}, folders: {} };
  }
}

export function saveThemeOverrides(o: ThemeOverrides): void {
  localStorage.setItem(OVERRIDES_KEY, JSON.stringify(o));
}

/** Resolve the terminal theme for a session, constrained to the app's current
 * light/dark mode. Session override wins, then the nearest folder up the path
 * ("a/b/c" checks a/b/c, a/b, a), then the mode's global pick, then the mode
 * default. A pick that belongs to the OTHER mode is skipped, so toggling the
 * app light/dark always yields a mode-appropriate terminal. */
export function resolveTermTheme(
  o: ThemeOverrides,
  globalPick: string,
  mode: TermMode,
  sessionKey: string,
  group?: string,
): string {
  const inMode = (name?: string) =>
    !!name && name in TERM_THEMES && TERM_THEMES[name].mode === mode;

  if (inMode(o.sessions[sessionKey])) return o.sessions[sessionKey];
  let g = group ?? "";
  while (g) {
    if (inMode(o.folders[g])) return o.folders[g];
    g = g.includes("/") ? g.slice(0, g.lastIndexOf("/")) : "";
  }
  if (inMode(globalPick)) return globalPick;
  return DEFAULT_BY_MODE[mode];
}
