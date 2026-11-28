import type { DiffFile } from "../../api";

export interface TreeDir { kind: "dir"; key: string; name: string; files: number; additions: number; deletions: number; children: TreeNode[] }
export interface TreeFile { kind: "file"; file: DiffFile; name: string }
export type TreeNode = TreeDir | TreeFile;

// Folders sort before files, each by name. A folder with one child folder and
// no files of its own folds into its child ("backend/entourage/services"),
// so a deep tree with few changes stays a few rows tall.
export function fileTree(files: DiffFile[]): TreeNode[] {
  const root: TreeDir = { kind: "dir", key: "", name: "", files: 0, additions: 0, deletions: 0, children: [] };
  for (const file of files) {
    const parts = file.path.split("/");
    let node = root;
    for (const part of parts.slice(0, -1)) {
      const key = node.key ? `${node.key}/${part}` : part;
      let next = node.children.find((child): child is TreeDir => child.kind === "dir" && child.key === key);
      if (!next) { next = { kind: "dir", key, name: part, files: 0, additions: 0, deletions: 0, children: [] }; node.children.push(next); }
      node = next;
    }
    node.children.push({ kind: "file", file, name: parts[parts.length - 1] });
  }
  return finish(root).children;
}

function finish(dir: TreeDir): TreeDir {
  let current = dir;
  while (current.key && current.children.length === 1 && current.children[0].kind === "dir") {
    const only = current.children[0];
    current = { ...only, name: `${current.name}/${only.name}` };
  }
  const children = current.children.map(child => child.kind === "dir" ? finish(child) : child);
  children.sort((a, b) => a.kind !== b.kind ? (a.kind === "dir" ? -1 : 1) : a.name.localeCompare(b.name));
  let files = 0, additions = 0, deletions = 0;
  for (const child of children) {
    if (child.kind === "dir") { files += child.files; additions += child.additions; deletions += child.deletions; }
    else { files += 1; additions += child.file.additions; deletions += child.file.deletions; }
  }
  return { ...current, children, files, additions, deletions };
}

// Tree order is reading order: Next and Prev walk the files as they are listed.
export function treeOrder(nodes: TreeNode[], collapsed: ReadonlySet<string> = new Set()): DiffFile[] {
  const out: DiffFile[] = [];
  const walk = (list: TreeNode[]) => {
    for (const node of list) {
      if (node.kind === "file") out.push(node.file);
      else if (!collapsed.has(node.key)) walk(node.children);
    }
  };
  walk(nodes);
  return out;
}

const GENERATED = /(_pb2\.py|\.pb\.go|_pb2_grpc\.py|\.lock|package-lock\.json|\.min\.(js|css)|\.generated\.\w+)$/;
export const TEST = /(^|\/)(tests?|__tests__|e2e|spec)\/|(_test\.(go|py)|\.test\.\w+|\.spec\.\w+|^test_[^/]+\.py|\/test_[^/]+\.py)$/;

export type FileKind = "gen" | "test" | "new" | "";

export function fileKind(file: DiffFile): FileKind {
  if (GENERATED.test(file.path)) return "gen";
  if (TEST.test(file.path)) return "test";
  if (file.status === "added") return "new";
  return "";
}
