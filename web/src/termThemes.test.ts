import { describe, expect, it } from "vitest";
import { resolveTermTheme, ThemeOverrides } from "./termThemes";

const none: ThemeOverrides = { sessions: {}, folders: {} };

describe("resolveTermTheme", () => {
  it("falls back to the global theme with no overrides", () => {
    expect(resolveTermTheme(none, "nord", "s1", "a/b")).toBe("nord");
  });

  it("a session override beats its folder's override", () => {
    const o: ThemeOverrides = {
      sessions: { s1: "dracula" },
      folders: { a: "paper" },
    };
    expect(resolveTermTheme(o, "duck", "s1", "a")).toBe("dracula");
    expect(resolveTermTheme(o, "duck", "s2", "a")).toBe("paper");
  });

  it("the NEAREST parent folder wins, walking up nested paths", () => {
    const o: ThemeOverrides = {
      sessions: {},
      folders: { a: "paper", "a/b": "nord" },
    };
    expect(resolveTermTheme(o, "duck", "s1", "a/b/c")).toBe("nord");
    expect(resolveTermTheme(o, "duck", "s1", "a/x")).toBe("paper");
    expect(resolveTermTheme(o, "duck", "s1", "z")).toBe("duck");
  });

  it("ignores override names that are no longer real themes", () => {
    const o: ThemeOverrides = {
      sessions: { s1: "deleted-theme" },
      folders: { a: "also-gone" },
    };
    expect(resolveTermTheme(o, "gruvbox-dark", "s1", "a")).toBe("gruvbox-dark");
  });
});
