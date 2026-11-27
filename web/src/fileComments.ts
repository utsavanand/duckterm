export interface StoredComment { id: string; quote: string; note: string; created_at: number; message_key?: string }
export type CommentIdentity = { artifactId: string; sha256: string } | { root: string; path: string; sha256: string };
export interface FileComment extends StoredComment { selection: string; sha256: string; current: boolean; page?: number }
// Never infer ownership from matching quote text. Old message annotations and
// unrelated artifacts may quote identical text; only explicit identity counts.
export function fileComments(comments: StoredComment[], identity: CommentIdentity): FileComment[] {
  return comments.flatMap(comment => {
    if (comment.message_key) return [];
    let context;
    try { context = JSON.parse(comment.quote); } catch { return []; }
    if (!context || typeof context !== "object" || typeof context.sha256 !== "string" || typeof context.selected_text !== "string") return [];
    const matches = "artifactId" in identity ? context.artifact_id === identity.artifactId
      : context.type === "file_feedback" && context.root === identity.root && context.path === identity.path;
    return matches ? [{ ...comment, selection: context.selected_text, sha256: context.sha256, current: context.sha256 === identity.sha256, page: Number.isInteger(context.pdf_page) && context.pdf_page > 0 && context.pdf_page <= 100000 ? context.pdf_page : undefined }] : [];
  });
}

export function isFileComment(quote: string): boolean {
  try { const value = JSON.parse(quote); return !!value && typeof value === "object" && (value.type === "file_feedback" || typeof value.artifact_id === "string"); } catch { return false; }
}
