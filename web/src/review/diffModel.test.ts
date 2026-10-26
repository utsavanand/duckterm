import { expect, it } from "vitest";
import type { DiffFile, DiffHunk } from "../api";
import { languageFor, nextUnviewed, splitRows, unifiedRows, visibleFiles } from "./diffModel";

const hunk: DiffHunk = { header: "def f():", old_start: 10, old_lines: 4, new_start: 12, new_lines: 5, lines: [
  { kind: " ", text: "a" }, { kind: "-", text: "b" }, { kind: "-", text: "c" }, { kind: "+", text: "B" }, { kind: " ", text: "d" }, { kind: "+", text: "e" }, { kind: "+", text: "f" }, { kind: "\\", text: " No newline at end of file" },
] };

it("numbers unified rows from each side's start and keeps the no-newline marker unnumbered", () => {
  const rows = unifiedRows(hunk);
  expect(rows.map(r => [r.kind, r.oldNumber, r.newNumber])).toEqual([
    [" ", 10, 12], ["-", 11, null], ["-", 12, null], ["+", null, 13], [" ", 13, 14], ["+", null, 15], ["+", null, 16], ["\\", null, null],
  ]);
});

it("pairs removed and added runs side by side and pads the shorter run", () => {
  const rows = splitRows(hunk);
  expect(rows.map(r => [r.left?.text ?? null, r.right?.text ?? null])).toEqual([
    ["a", "a"], ["b", "B"], ["c", null], ["d", "d"], [null, "e"], [null, "f"],
  ]);
  expect(rows[1].left?.number).toBe(11);
  expect(rows[1].right?.number).toBe(13);
  expect(rows[4].right?.number).toBe(15);
});

const file = (path: string, extra: Partial<DiffFile> = {}): DiffFile => ({
  path, old_path: null, status: "modified", binary: false, additions: 1, deletions: 0, hunks: [], truncated: false, total_lines: 1,
  bucket: "read", bucket_rule: "read", rules: [], hash: "h", viewed: false, stale: false, changed_since: null, whitespace_only: false, ...extra,
});

it("filters to unviewed files and to files changed since the last look", () => {
  const files = [file("a", { viewed: true, changed_since: true }), file("b", { changed_since: false }), file("c", { changed_since: true })];
  expect(visibleFiles(files, { onlyUnviewed: true, sinceOnly: false }).map(f => f.path)).toEqual(["b", "c"]);
  expect(visibleFiles(files, { onlyUnviewed: false, sinceOnly: true }).map(f => f.path)).toEqual(["a", "c"]);
  expect(visibleFiles(files, { onlyUnviewed: true, sinceOnly: true }).map(f => f.path)).toEqual(["c"]);
});

it("finds the next unviewed file after the current one and wraps around", () => {
  const files = [file("a", { viewed: true }), file("b"), file("c", { viewed: true }), file("d")];
  expect(nextUnviewed(files, "b")?.path).toBe("d");
  expect(nextUnviewed(files, "d")?.path).toBe("b");
  expect(nextUnviewed(files, null)?.path).toBe("b");
  expect(nextUnviewed([file("a", { viewed: true })], "a")).toBeNull();
});

it("maps file extensions to registered grammars", () => {
  expect(languageFor("src/duckterm/server.py")).toBe("python");
  expect(languageFor("web/src/App.tsx")).toBe("typescript");
  expect(languageFor("README")).toBeNull();
  expect(languageFor("logo.png")).toBeNull();
});
