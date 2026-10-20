import type { ITheme } from "@xterm/xterm";
import { TERM_THEMES, DEFAULT_TERM_THEME, type TermTheme } from "./termThemes";

const channels = (hex: string) => [1, 3, 5].map(i => parseInt(hex.slice(i, i + 2), 16));
const blend = (a: string, b: string, amount: number) => {
  const from = channels(a), to = channels(b);
  return "#" + from.map((v, i) => Math.round(v + (to[i] - v) * amount).toString(16).padStart(2, "0")).join("");
};

// Extended ANSI is a color cube followed by a gray ramp. Programs such as
// Claude choose these indices explicitly, bypassing the theme's 16 ANSI colors.
// Recolor the palette itself: existing buffer cells, scrollback, selections and
// future output keep their original indices and require no replay or PTY edits.
function extendedColor(index: number, theme: TermTheme): string {
  const levels = [0, 95, 135, 175, 215, 255];
  const n = index - 16;
  const rgb = index >= 232 ? [8 + (index - 232) * 10, 8 + (index - 232) * 10, 8 + (index - 232) * 10]
    : [levels[Math.floor(n / 36)], levels[Math.floor(n / 6) % 6], levels[n % 6]];
  const low = Math.min(...rgb), high = Math.max(...rgb), range = high - low;
  // White text (including Claude's index231) follows the theme foreground.
  // Dark neutral fills follow its background, also when changing to light mode.
  if (range === 0) return blend(theme.background, theme.foreground, high / 255);
  const [r, g, b] = rgb;
  let hue = high === r ? (g - b) / range : high === g ? 2 + (b - r) / range : 4 + (r - g) / range;
  hue = (hue + 6) % 6;
  const accents = [theme.red, theme.yellow, theme.green, theme.cyan, theme.blue, theme.magenta];
  const sector = Math.floor(hue);
  const accent = blend(accents[sector], accents[(sector + 1) % 6], hue - sector);
  const shaded = blend(theme.background, accent, high / 255);
  return blend(shaded, blend(theme.background, theme.foreground, high / 255), low / high);
}

const cache = new Map<string, ITheme>();
export function terminalPalette(name: string, preserve = false): ITheme {
  const theme = TERM_THEMES[name] ?? TERM_THEMES[DEFAULT_TERM_THEME];
  if (preserve) return theme;
  const key = name in TERM_THEMES ? name : DEFAULT_TERM_THEME;
  let palette = cache.get(key);
  if (!palette) {
    palette = { ...theme, extendedAnsi: Array.from({ length: 240 }, (_, i) => extendedColor(i + 16, theme)) };
    cache.set(key, palette);
  }
  return palette;
}
