import { expect, it } from "vitest";
import { terminalPalette } from "./terminalPalette";
import { TERM_THEMES } from "./termThemes";

it("maps indexed white and black to the selected foreground and background", () => {
  for (const [name, theme] of Object.entries(TERM_THEMES)) {
    const palette = terminalPalette(name);
    expect(palette.extendedAnsi).toHaveLength(240);
    expect(palette.extendedAnsi?.[231 - 16]).toBe(theme.foreground);
    expect(palette.extendedAnsi?.[16 - 16]).toBe(theme.background);
    expect(palette.extendedAnsi?.every(c => /^#[0-9a-f]{6}$/.test(c))).toBe(true);
  }
});
it("uses theme accents for indexed colors and retains dark-fill distinction", () => {
  const duck = terminalPalette("duck"), gruv = terminalPalette("gruvbox-dark");
  expect(duck.extendedAnsi?.[46 - 16]).toBe(TERM_THEMES.duck.green);
  expect(gruv.extendedAnsi?.[46 - 16]).toBe(TERM_THEMES["gruvbox-dark"].green);
  expect(gruv.extendedAnsi?.[77 - 16]).not.toBe(duck.extendedAnsi?.[77 - 16]);
  expect(gruv.extendedAnsi?.[22 - 16]).not.toBe(gruv.extendedAnsi?.[77 - 16]);
});
it("preserves original extended colors on explicit opt-out and handles an unknown theme", () => {
  expect(terminalPalette("duck", true).extendedAnsi).toBeUndefined();
  expect(terminalPalette("missing")).toEqual(terminalPalette("duck"));
});
