import { describe, expect, it } from "vitest";
import type { DiffFile } from "../../api";
import { fileKind, fileTree, treeOrder, type TreeDir } from "./tree";

function file(path: string, additions = 1, deletions = 0, status: DiffFile["status"] = "modified"): DiffFile {
  return { path, old_path: null, status, binary: false, additions, deletions, hunks: [], truncated: false, total_lines: 0, bucket: "read", bucket_rule: "read", rules: [], hash: path, viewed: false, stale: false, changed_since: null, whitespace_only: false };
}

describe("fileTree", () => {
  it("folds single-child folders and rolls up counts", () => {
    const tree = fileTree([
      file("backend/entourage/services/auth_service.py", 24, 26),
      file("backend/entourage/services/llm_providers.py", 26, 13),
      file("backend/entourage/managers/llm_manager.py", 14, 10),
      file("gateway/main.go", 1, 0),
      file("README.md", 2, 1),
    ]);
    expect(tree.map(node => node.name)).toEqual(["backend/entourage", "gateway", "README.md"]);
    const backend = tree[0] as TreeDir;
    expect(backend.children.map(node => node.name)).toEqual(["managers", "services"]);
    expect([backend.files, backend.additions, backend.deletions]).toEqual([3, 64, 49]);
  });

  it("walks files in the order the tree lists them and skips collapsed folders", () => {
    const tree = fileTree([file("b/z.py"), file("a/y.py"), file("a/x.py"), file("top.py")]);
    expect(treeOrder(tree).map(f => f.path)).toEqual(["a/x.py", "a/y.py", "b/z.py", "top.py"]);
    expect(treeOrder(tree, new Set(["a"])).map(f => f.path)).toEqual(["b/z.py", "top.py"]);
  });
});

describe("fileKind", () => {
  it.each([
    ["backend/entourage/proto/auth_pb2.py", "gen"],
    ["gateway/proto/auth.pb.go", "gen"],
    ["gateway/handlers/ai_connection_test.go", "test"],
    ["backend/tests/services/test_ai_connection.py", "test"],
    ["frontend/e2e/ai-connections.spec.ts", "test"],
    ["web/src/Home.test.tsx", "test"],
    ["gateway/handlers/auth.go", ""],
  ])("%s is %s", (path, kind) => { expect(fileKind(file(path))).toBe(kind); });

  it("marks an added non-test file as new", () => {
    expect(fileKind(file("backend/entourage/services/ai_connection.py", 69, 0, "added"))).toBe("new");
  });
});
