import { authHeaders, type Artifact } from "./api";
import type { SessionView } from "./types";

export type FileSession = Pick<SessionView, "key" | "host" | "cwd" | "worktreePath">;
export type SourceState = "present" | "missing" | "outside_root" | "unavailable";
export interface RegisteredFile extends Artifact { path: string | null; source_state: SourceState; content_only: boolean; }
export interface FileEntry {
  name: string; path: string; type: "directory" | "file" | "symlink" | "other";
  size: number | null; modified_at: number; browsable: boolean; content_limit: number; artifact: RegisteredFile | null;
}
export interface DirectoryPage {
  available: true; root: string; path: string; entries: FileEntry[]; next_cursor: string | null;
  truncated: boolean; limit_reached: boolean; ignore_status: "applied"; artifacts: RegisteredFile[];
}
export interface FilesUnavailable { available: false; reason: string; artifacts?: RegisteredFile[]; size?: number; content_limit?: number; }
export interface FileContent { available: true; path: string; name: string; size: number; modified_at: number; media_type: string; content_base64: string; content_limit: number; sha256: string; }
export class FileRequestError extends Error { constructor(message: string, readonly status: number) { super(message); } }
// Guard before ANY routing: a raw key paired with a remote host is remote too.
export function localFileSession(session: FileSession) { return (!session.host || session.host === "local") && !session.key.startsWith("~remote~"); }
async function request<T>(session: FileSession, operation: string, query: URLSearchParams, signal?: AbortSignal): Promise<T> {
  if (!localFileSession(session)) throw new FileRequestError("Remote — not browsable here", 403);
  // This feature is local-only. Do not pass through routedFetch/hostTransport.
  const response = await globalThis.fetch(`/sessions/${encodeURIComponent(session.key)}/${operation}?${query}`, { headers: authHeaders(), cache: "no-store", signal });
  const result = await response.json();
  if (!response.ok) throw new FileRequestError(result.error || "Files could not be loaded", response.status);
  return result as T;
}
export const sessionFiles = {
  list: (session: FileSession, path = ".", cursor?: string, signal?: AbortSignal) => {
    const query = new URLSearchParams({ path }); if (cursor) query.set("cursor", cursor);
    return request<DirectoryPage | FilesUnavailable>(session, "files", query, signal);
  },
  content: (session: FileSession, path: string, signal?: AbortSignal) => request<FileContent | FilesUnavailable>(session, "file-content", new URLSearchParams({ path }), signal),
};
export function fileReason(reason: string): string {
  return ({ remote_not_browsable: "Remote — not browsable here", directory_not_recorded: "No working directory is recorded for this session.", directory_missing: "The working directory is missing or moved.", directory_unreadable: "The working directory could not be read.", file_missing: "This file is missing or moved. Refresh the directory.", file_unreadable: "This file could not be read.", file_too_large: "This file exceeds the 5 MiB preview and download limit." } as Record<string, string>)[reason] || reason;
}
export function sourceLabel(state: SourceState): string {
  return ({ present: "Registered file in this worktree", missing: "Content-only · original missing", outside_root: "Saved copy · outside this worktree", unavailable: "Saved copy · original unavailable" })[state];
}
export function fileSize(bytes: number | null) { return bytes === null ? "—" : bytes < 1024 ? `${bytes} B` : bytes < 1048576 ? `${(bytes / 1024).toFixed(1)} KB` : `${(bytes / 1048576).toFixed(1)} MB`; }
