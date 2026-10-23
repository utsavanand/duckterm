import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, type Bucket, type DiffFile, type DiffHunk, type ReviewDiff, type ReviewHeader } from "../api";
import { highlighter } from "../render";
import { BUCKET_LABELS, groupByBucket, languageFor, nextUnviewed, shortSha, splitRows, unifiedRows, visibleFiles } from "./diffModel";

type Layout = "unified" | "split";

function fileName(path: string): { dir: string; name: string } {
  const slash = path.lastIndexOf("/");
  return slash < 0 ? { dir: "", name: path } : { dir: path.slice(0, slash + 1), name: path.slice(slash + 1) };
}

function Code({ text, language }: { text: string; language: string | null }) {
  const html = useMemo(() => language ? highlighter.highlight(text, { language, ignoreIllegals: true }).value : null, [text, language]);
  if (html === null) return <code>{text}</code>;
  return <code dangerouslySetInnerHTML={{ __html: html }} />;
}

export function DiffPane({ header, onAsk }: { header: ReviewHeader; onAsk: (quote: string) => void }) {
  const { repo, ref } = header;
  const [diff, setDiff] = useState<ReviewDiff | null>(null);
  const [error, setError] = useState("");
  const [layout, setLayout] = useState<Layout>("unified");
  const [whitespace, setWhitespace] = useState(true);
  const [onlyUnviewed, setOnlyUnviewed] = useState(false);
  const [sinceOnly, setSinceOnly] = useState(false);
  const [selected, setSelected] = useState<string | null>(null);
  const alive = useRef(true);
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);
  const load = useCallback(async () => {
    try {
      const result = await api.reviewDiff(repo, ref, { whitespace, since: header.previous_head });
      if (!alive.current) return;
      setDiff(result);
      setError("");
      setSelected(current => current && result.files.some(file => file.path === current) ? current : result.files[0]?.path ?? null);
    } catch (cause) {
      if (alive.current) setError((cause as Error).message);
    }
  }, [repo, ref, whitespace, header.previous_head]);
  useEffect(() => { void load(); }, [load]);

  const files = useMemo(() => diff?.files ?? [], [diff]);
  const shown = useMemo(() => visibleFiles(files, { onlyUnviewed, sinceOnly }), [files, onlyUnviewed, sinceOnly]);
  const current = files.find(file => file.path === selected) ?? null;

  async function mark(file: DiffFile, viewed: boolean) {
    setDiff(previous => previous && { ...previous, files: previous.files.map(f => f.path === file.path ? { ...f, viewed, stale: false } : f), counts: { ...previous.counts, viewed: previous.counts.viewed + (viewed ? 1 : -1) } });
    try { await api.markViewed(repo, ref, { path: file.path, hash: file.hash, viewed }); }
    catch (cause) { setError((cause as Error).message); void load(); }
  }
  async function move(file: DiffFile, bucket: Bucket) {
    setDiff(previous => previous && { ...previous, files: previous.files.map(f => f.path === file.path ? { ...f, bucket } : f) });
    try { await api.markViewed(repo, ref, { path: file.path, hash: file.hash, bucket: bucket === file.bucket_rule ? "" : bucket }); }
    catch (cause) { setError((cause as Error).message); void load(); }
  }
  function step(direction: 1 | -1) {
    if (!current) return;
    const index = shown.findIndex(file => file.path === current.path);
    const next = shown[index + direction];
    if (next) setSelected(next.path);
  }

  return <div className="rd-diff">
    <div className="rd-diff-toolbar" role="toolbar" aria-label="Diff options">
      <span className="rd-diff-range" title={`${diff?.base_sha ?? header.base_sha} → ${diff?.head_sha ?? header.head_sha}`}>{header.base} {shortSha(header.base_sha)} → {header.branch} {shortSha(header.head_sha)}</span>
      <div className="rd-diff-seg" role="group" aria-label="Layout">
        <button aria-pressed={layout === "unified"} onClick={() => setLayout("unified")}>Unified</button>
        <button aria-pressed={layout === "split"} onClick={() => setLayout("split")}>Split</button>
      </div>
      <label><input type="checkbox" checked={whitespace} onChange={event => setWhitespace(event.target.checked)} /> Hide whitespace</label>
      <label><input type="checkbox" checked={onlyUnviewed} onChange={event => setOnlyUnviewed(event.target.checked)} /> Only unviewed</label>
      {header.previous_head && diff && <label title={`Files whose hunks changed since ${shortSha(header.previous_head)}`}><input type="checkbox" checked={sinceOnly} onChange={event => setSinceOnly(event.target.checked)} /> Since my last look · {diff.counts.changed_since} {diff.counts.changed_since === 1 ? "file" : "files"}</label>}
      {diff && <span className="rd-diff-progress">{diff.counts.viewed}/{files.length} viewed</span>}
    </div>
    {error && <p className="rd-prs-note" role="alert">{error}</p>}
    {!diff && !error && <p className="rd-prs-empty" role="status">Reading the diff against {header.base}…</p>}
    {diff && files.length === 0 && <p className="rd-prs-empty">No changes between {header.base} and {header.branch}.</p>}
    {diff && files.length > 0 && <div className="rd-diff-body">
      <nav className="rd-diff-files" aria-label="Changed files">
        {groupByBucket(shown).map(group => <section key={group.bucket} className={`rd-diff-group is-${group.bucket}`}>
          <h3>{BUCKET_LABELS[group.bucket]} <span>{group.files.length}</span></h3>
          <ul>{group.files.map(file => {
            const { dir, name } = fileName(file.path);
            return <li key={file.path} className={file.path === selected ? "selected" : undefined}>
              <input type="checkbox" aria-label={`Viewed ${file.path}`} checked={file.viewed} onChange={event => void mark(file, event.target.checked)} />
              <button className="rd-diff-file" onClick={() => setSelected(file.path)} title={file.path}>
                <span className="dir">{dir}</span><span className="name">{name}</span>
                {file.stale && <span className="rd-diff-stale" title="Changed since you viewed it">changed</span>}
                {file.changed_since && sinceOnly === false && header.previous_head && <span className="rd-diff-stale">since</span>}
              </button>
              <span className="rd-diff-counts"><span className="add">+{file.additions}</span> <span className="del">−{file.deletions}</span></span>
              <select className="rd-diff-move" aria-label={`Bucket for ${file.path}`} value={file.bucket} onChange={event => void move(file, event.target.value as Bucket)}>
                {(Object.keys(BUCKET_LABELS) as Bucket[]).map(bucket => <option key={bucket} value={bucket}>{BUCKET_LABELS[bucket]}</option>)}
              </select>
            </li>;
          })}</ul>
        </section>)}
        {shown.length === 0 && <p className="rd-prs-empty">Every file is viewed.</p>}
      </nav>
      {current && <FileView key={current.path} file={current} header={header} layout={layout} onAsk={onAsk}
        onPrev={() => step(-1)} onNext={() => { const next = nextUnviewed(shown, current.path); if (next) setSelected(next.path); }}
        onViewed={() => void mark(current, !current.viewed)} hasNextUnviewed={nextUnviewed(shown, current.path) !== null} />}
    </div>}
  </div>;
}

function FileView({ file, header, layout, onAsk, onPrev, onNext, onViewed, hasNextUnviewed }: {
  file: DiffFile; header: ReviewHeader; layout: Layout; onAsk: (quote: string) => void;
  onPrev: () => void; onNext: () => void; onViewed: () => void; hasNextUnviewed: boolean;
}) {
  const language = languageFor(file.path);
  const blob = header.github ? `https://github.com/${header.github}/blob/${header.head_sha}/${file.path}` : null;
  const reasons = file.rules.filter(rule => rule.bucket === file.bucket_rule).map(rule => rule.reason);
  return <article className="rd-diff-view" aria-label={file.path}>
    <header className="rd-diff-file-head">
      <div className="rd-diff-file-title">
        <strong>{file.path}</strong>
        {file.old_path && <small>renamed from {file.old_path}</small>}
        <small>{file.status}{file.binary ? " · binary" : ` · ${file.hunks.length} ${file.hunks.length === 1 ? "hunk" : "hunks"}`} · <span className="add">+{file.additions}</span> <span className="del">−{file.deletions}</span></small>
      </div>
      <div className="rd-diff-file-actions">
        <button className="rd-btn rd-btn-ghost rd-btn-sm" onClick={onPrev}>Prev</button>
        <button className="rd-btn rd-btn-ghost rd-btn-sm" onClick={onNext} disabled={!hasNextUnviewed}>Next unviewed</button>
        <button className={`rd-btn rd-btn-sm ${file.viewed ? "rd-btn-ghost" : "rd-btn-primary"}`} aria-pressed={file.viewed} onClick={onViewed}>{file.viewed ? "Viewed ✓" : "Mark viewed"}</button>
      </div>
    </header>
    <p className={`rd-diff-why is-${file.bucket}`}>
      <strong>{BUCKET_LABELS[file.bucket]}</strong>
      {file.bucket !== file.bucket_rule ? ` · moved by you from ${BUCKET_LABELS[file.bucket_rule]}` : ""}
      {reasons.length > 0 ? ` · ${reasons.join("; ")}` : ""}
    </p>
    {file.binary && <p className="rd-prs-empty">Binary file; nothing to show inline.</p>}
    {file.truncated && <p className="rd-prs-note">Showing the first {file.hunks.reduce((n, hunk) => n + hunk.lines.length, 0)} of {file.total_lines} lines.{blob && <> <a href={blob} target="_blank" rel="noreferrer">Open full file</a></>}</p>}
    {file.hunks.map((hunk, index) => <HunkView key={index} hunk={hunk} language={language} layout={layout} onAsk={() => onAsk(`${file.path} @@ ${hunk.header || `hunk ${index + 1}`}`)} />)}
  </article>;
}

function HunkView({ hunk, language, layout, onAsk }: { hunk: DiffHunk; language: string | null; layout: Layout; onAsk: () => void }) {
  const label = `@@ -${hunk.old_start},${hunk.old_lines} +${hunk.new_start},${hunk.new_lines} @@`;
  return <div className="rd-hunk">
    <div className="rd-hunk-head">
      <code>{label} {hunk.header}</code>
      <button className="rd-hunk-ask" onClick={onAsk}>Ask about this hunk</button>
    </div>
    {layout === "unified" ? <table className="rd-hunk-table unified"><tbody>
      {unifiedRows(hunk).map((row, index) => <tr key={index} className={`k${row.kind === "\\" ? "n" : row.kind.trim() || "c"}`}>
        <td className="num">{row.oldNumber ?? ""}</td><td className="num">{row.newNumber ?? ""}</td>
        <td className="sign">{row.kind === "\\" ? "" : row.kind.trim()}</td>
        <td className="text">{row.kind === "\\" ? <code className="rd-hunk-note">{row.text.trim()}</code> : <Code text={row.text} language={language} />}</td>
      </tr>)}
    </tbody></table> : <table className="rd-hunk-table split"><tbody>
      {splitRows(hunk).map((row, index) => <tr key={index}>
        <td className={`num ${row.left ? `k${row.left.kind.trim() || "c"}` : "empty"}`}>{row.left?.number ?? ""}</td>
        <td className={`text ${row.left ? `k${row.left.kind.trim() || "c"}` : "empty"}`}>{row.left && <Code text={row.left.text} language={language} />}</td>
        <td className={`num ${row.right ? `k${row.right.kind.trim() || "c"}` : "empty"}`}>{row.right?.number ?? ""}</td>
        <td className={`text ${row.right ? `k${row.right.kind.trim() || "c"}` : "empty"}`}>{row.right && <Code text={row.right.text} language={language} />}</td>
      </tr>)}
    </tbody></table>}
  </div>;
}
