import type { Bucket, DiffFile, DiffHunk, DiffLine } from "../api";

export const BUCKET_LABELS: Record<Bucket, string> = { high: "High risk", read: "Worth a read", skim: "Safe to skim" };
export const BUCKET_ORDER: Bucket[] = ["high", "read", "skim"];

const LANGUAGES: Record<string, string> = {
  py: "python", ts: "typescript", tsx: "typescript", js: "javascript", jsx: "javascript", mjs: "javascript", cjs: "javascript",
  json: "json", css: "css", html: "xml", xml: "xml", svg: "xml", yml: "yaml", yaml: "yaml", sh: "bash", bash: "bash", zsh: "bash",
  go: "go", rs: "rust", sql: "sql",
};

export function languageFor(path: string): string | null {
  const dot = path.lastIndexOf(".");
  if (dot < 0) return path.endsWith("Makefile") ? "bash" : null;
  return LANGUAGES[path.slice(dot + 1).toLowerCase()] ?? null;
}

export interface UnifiedRow { kind: DiffLine["kind"]; text: string; oldNumber: number | null; newNumber: number | null }

export function unifiedRows(hunk: DiffHunk): UnifiedRow[] {
  let oldNumber = hunk.old_start;
  let newNumber = hunk.new_start;
  const rows: UnifiedRow[] = [];
  for (const line of hunk.lines) {
    if (line.kind === "\\") { rows.push({ kind: line.kind, text: line.text, oldNumber: null, newNumber: null }); continue; }
    const row: UnifiedRow = { kind: line.kind, text: line.text, oldNumber: null, newNumber: null };
    if (line.kind !== "+") row.oldNumber = oldNumber++;
    if (line.kind !== "-") row.newNumber = newNumber++;
    rows.push(row);
  }
  return rows;
}

export interface SplitCell { kind: DiffLine["kind"]; text: string; number: number }
export interface SplitRow { left: SplitCell | null; right: SplitCell | null }

// Pairs each run of removed lines with the run of added lines that follows it,
// the way GitHub's split view does; context lines sit on both sides.
export function splitRows(hunk: DiffHunk): SplitRow[] {
  const rows: SplitRow[] = [];
  let oldNumber = hunk.old_start;
  let newNumber = hunk.new_start;
  let index = 0;
  const lines = hunk.lines.filter(line => line.kind !== "\\");
  while (index < lines.length) {
    const line = lines[index];
    if (line.kind === " ") {
      rows.push({ left: { kind: " ", text: line.text, number: oldNumber++ }, right: { kind: " ", text: line.text, number: newNumber++ } });
      index += 1;
      continue;
    }
    const removed: SplitCell[] = [];
    const added: SplitCell[] = [];
    while (index < lines.length && lines[index].kind === "-") removed.push({ kind: "-", text: lines[index++].text, number: oldNumber++ });
    while (index < lines.length && lines[index].kind === "+") added.push({ kind: "+", text: lines[index++].text, number: newNumber++ });
    for (let i = 0; i < Math.max(removed.length, added.length); i += 1) rows.push({ left: removed[i] ?? null, right: added[i] ?? null });
  }
  return rows;
}

export interface Filters { onlyUnviewed: boolean; sinceOnly: boolean }

export function visibleFiles(files: DiffFile[], filters: Filters): DiffFile[] {
  return files.filter(file => (!filters.onlyUnviewed || !file.viewed) && (!filters.sinceOnly || file.changed_since !== false));
}

export function nextUnviewed(files: DiffFile[], current: string | null): DiffFile | null {
  const start = current ? files.findIndex(file => file.path === current) : -1;
  for (let step = 1; step <= files.length; step += 1) {
    const candidate = files[(start + step) % files.length];
    if (!candidate.viewed && candidate.path !== current) return candidate;
  }
  return null;
}

export function shortSha(sha: string | null | undefined): string {
  return sha ? sha.slice(0, 7) : "";
}

export function groupByBucket(files: DiffFile[]): { bucket: Bucket; files: DiffFile[] }[] {
  return BUCKET_ORDER.map(bucket => ({ bucket, files: files.filter(file => file.bucket === bucket) })).filter(group => group.files.length > 0);
}
