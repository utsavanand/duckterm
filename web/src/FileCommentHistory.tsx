import type { FileComment } from "./fileComments";
export function FileCommentHistory({ comments, error, loading, onLocate, onClose, retry }: { comments: FileComment[]; error: string; loading: boolean; onLocate: (quote: string) => void; onClose: () => void; retry: () => void }) {
  const current = comments.filter(c => c.current), previous = comments.filter(c => !c.current);
  const comment = (c: FileComment) => <article key={c.id} className="rd-file-comment"><time dateTime={new Date(c.created_at).toISOString()}>{new Date(c.created_at).toLocaleString()}</time>{c.selection && <blockquote>{c.selection}</blockquote>}<p>{c.note}</p>{c.current && c.selection && <button className="rd-file-saved-link" onClick={() => onLocate(c.selection)}>Show quote</button>}</article>;
  return <aside className="rd-file-comments" aria-label="Comment history"><header><strong>Comments</strong><button className="rd-btn rd-btn-sm" onClick={onClose} aria-label="Close comments">×</button></header>
    {loading && <p role="status">Loading comments…</p>}{error && <p role="alert">{error} <button onClick={retry}>Retry</button></p>}
    {!loading && !error && !current.length && <p>No comments on this version yet.</p>}{current.map(comment)}
    {!!previous.length && <details><summary>Earlier versions ({previous.length})</summary><p>These comments refer to different content. They are not attached to this version.</p>{previous.map(comment)}</details>}
  </aside>;
}
