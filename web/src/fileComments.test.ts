import { expect, it } from "vitest";
import { fileComments, isFileComment } from "./fileComments";
it("scopes comments by identity and version, never by a coincidentally matching quote", () => {
  const row = (context: object, id = "1") => ({ id, quote: JSON.stringify(context), note: "Fix", created_at: 1 });
  const base = { artifact_id: "a", sha256: "current", selected_text: "Same words" };
  const rows = [row(base), row({ ...base, sha256: "old" }, "2"), row({ ...base, artifact_id: "b" }), row({ type: "file_feedback", root: "/repo", path: "a.md", sha256: "current", selected_text: "Same words" }), row({ quote: "Same words" })];
  expect(fileComments(rows, { artifactId: "a", sha256: "current" }).map(r => [r.id, r.current])).toEqual([["1", true], ["2", false]]);
  expect(fileComments(rows, { root: "/repo", path: "a.md", sha256: "current" })).toHaveLength(1);
  expect(fileComments(rows, { root: "/other", path: "a.md", sha256: "current" })).toHaveLength(0);
  expect(isFileComment(rows[0].quote)).toBe(true);
  expect(isFileComment("Ordinary message quote")).toBe(false);
});
