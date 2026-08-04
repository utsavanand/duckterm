// Terminal color themes (xterm.js ITheme palettes). Full 16-color ANSI sets —
// TUIs render wrong without them. "duck" is the shipped default.

export interface TermTheme {
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
    background: "#0c0f16",
    foreground: "#d1d5db",
    cursor: "#4ade80",
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
    background: "#ffffff",
    foreground: "#1f2328",
    cursor: "#178a3f",
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
};

export const DEFAULT_TERM_THEME = "duck";

export function loadTermTheme(): string {
  const saved = localStorage.getItem("rd-term-theme");
  return saved && saved in TERM_THEMES ? saved : DEFAULT_TERM_THEME;
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

/** Session override wins, then the nearest folder up the path ("a/b/c" checks
 * a/b/c, a/b, a), then the global choice. Unknown names are ignored so a
 * stale saved override can't blank a terminal. */
export function resolveTermTheme(
  o: ThemeOverrides,
  globalTheme: string,
  sessionKey: string,
  group?: string,
): string {
  const s = o.sessions[sessionKey];
  if (s && s in TERM_THEMES) return s;
  let g = group ?? "";
  while (g) {
    const f = o.folders[g];
    if (f && f in TERM_THEMES) return f;
    g = g.includes("/") ? g.slice(0, g.lastIndexOf("/")) : "";
  }
  return globalTheme in TERM_THEMES ? globalTheme : DEFAULT_TERM_THEME;
}
