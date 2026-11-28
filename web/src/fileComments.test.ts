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

it("keeps page locations version-scoped and ignores malformed page metadata", () => {
  const values = [2, true, 0, -1, 1.5, "2", 100001, undefined];
  const rows = values.map((pdf_page, i) => ({ id: String(i), quote: JSON.stringify({artifact_id:"a",sha256:i ? "old" : "current",selected_text:"",pdf_page}), note:"Diagram", created_at:1 }));
  const comments = fileComments(rows, {artifactId:"a",sha256:"current"});
  expect(comments.map(row=>row.page)).toEqual([2,undefined,undefined,undefined,undefined,undefined,undefined,undefined]);
  expect(comments.map(row=>row.current)).toEqual([true,false,false,false,false,false,false,false]);
});
