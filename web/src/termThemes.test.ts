import { describe, expect, it } from "vitest";
import {
  AUTO,
  DEFAULT_BY_MODE,
  ThemeOverrides,
  resolveTermTheme,
  themesForMode,
} from "./termThemes";

const none: ThemeOverrides = { sessions: {}, folders: {} };

describe("resolveTermTheme (mode-driven)", () => {
  it("with no picks, falls back to the mode's default theme", () => {
    expect(resolveTermTheme(none, AUTO, "dark", "s1", "a/b")).toBe(
      DEFAULT_BY_MODE.dark,
    );
    expect(resolveTermTheme(none, AUTO, "light", "s1", "a/b")).toBe(
      DEFAULT_BY_MODE.light,
    );
  });

  it("a session override beats its folder's override (same mode)", () => {
    const o: ThemeOverrides = {
      sessions: { s1: "dracula" },
      folders: { a: "nord" },
    };
    expect(resolveTermTheme(o, AUTO, "dark", "s1", "a")).toBe("dracula");
    expect(resolveTermTheme(o, AUTO, "dark", "s2", "a")).toBe("nord");
  });

  it("the NEAREST parent folder wins, walking up nested paths", () => {
    const o: ThemeOverrides = {
      sessions: {},
      folders: { a: "gruvbox-dark", "a/b": "nord" },
    };
    expect(resolveTermTheme(o, AUTO, "dark", "s1", "a/b/c")).toBe("nord");
    expect(resolveTermTheme(o, AUTO, "dark", "s1", "a/x")).toBe("gruvbox-dark");
    expect(resolveTermTheme(o, "duck", "dark", "s1", "z")).toBe("duck");
  });

  it("skips picks that belong to the OTHER mode, so toggling stays in-mode", () => {
    // A dark override + dark global, but the app is in LIGHT mode: none apply,
    // so it resolves to the light default rather than dragging a dark theme in.
    const o: ThemeOverrides = {
      sessions: { s1: "dracula" },
      folders: { a: "nord" },
    };
    expect(resolveTermTheme(o, "duck", "light", "s1", "a")).toBe(
      DEFAULT_BY_MODE.light,
    );
    // In dark mode those same picks DO apply.
    expect(resolveTermTheme(o, "duck", "dark", "s1", "a")).toBe("dracula");
  });

  it("ignores override names that are no longer real themes", () => {
    const o: ThemeOverrides = {
      sessions: { s1: "deleted-theme" },
      folders: { a: "also-gone" },
    };
    expect(resolveTermTheme(o, "gruvbox-dark", "dark", "s1", "a")).toBe(
      "gruvbox-dark",
    );
  });

  it("themesForMode returns only that mode's themes", () => {
    expect(themesForMode("light")).toContain("paper");
    expect(themesForMode("light")).not.toContain("duck");
    expect(themesForMode("dark")).toContain("duck");
    expect(themesForMode("dark")).not.toContain("paper");
  });
});
