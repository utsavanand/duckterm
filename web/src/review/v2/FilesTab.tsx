import { useEffect, useMemo, useRef, useState } from "react";
import { api, type DiffFile, type DiffHunk, type ReviewDiff, type ReviewHeader } from "../../api";
import { modelHtml } from "../../render";
import { Code } from "../DiffPane";
import { languageFor, splitRows, unifiedRows } from "../diffModel";
import { fileKind, fileTree, treeOrder, type TreeNode } from "./tree";

export interface LineNote { path: string; from: number; to: number; text: string }
export interface FileFilter { label: string; prefix: string }
type Layout = "unified" | "split";
// A selected range of lines in one hunk; numbers are the new-file lines, or
// the old-file lines for removed lines.
interface Selection { path: string; hunk: number; from: number; to: number; anchor: number }
interface Answer { q: string; a: string; citations: { path: string; line: number }[] }

function rowNumber(kind: string, oldNumber: number | null, newNumber: number | null): number {
  return kind === "-" ? oldNumber ?? 0 : newNumber ?? 0;
}

export function FilesTab({ header, diff, error, whitespace, onWhitespace, focus, filter, onClearFilter, onMark, onNote }: {
  header: ReviewHeader; diff: ReviewDiff | null; error: string; whitespace: boolean; onWhitespace: (value: boolean) => void;
  focus: { path: string; line?: number; nonce: number } | null; filter: FileFilter | null; onClearFilter: () => void;
  onMark: (file: DiffFile, viewed: boolean) => void; onNote: (note: LineNote) => void;
}) {
  const [layout, setLayout] = useState<Layout>("unified");
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());
  const [selected, setSelected] = useState<string | null>(null);
  const files = useMemo(() => diff?.files ?? [], [diff]);
  const tree = useMemo(() => fileTree(files), [files]);
  const order = useMemo(() => treeOrder(tree), [tree]);
  useEffect(() => {
    setSelected(current => current && files.some(f => f.path === current) ? current : (filter ? order.find(f => f.path.startsWith(filter.prefix)) : order[0])?.path ?? null);
  }, [files, order, filter]);
  useEffect(() => { if (focus && files.some(f => f.path === focus.path)) setSelected(focus.path); }, [focus, files]);
  const current = files.find(f => f.path === selected) ?? null;

  function step(direction: 1 | -1) {
    const index = order.findIndex(f => f.path === selected);
    const next = order[index + direction];
    if (next) setSelected(next.path);
  }
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null;
      if (target && ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName)) return;
      if (event.metaKey || event.ctrlKey || event.altKey) return;
      if (event.key === "j") step(1);
      else if (event.key === "k") step(-1);
      else if (event.key === "v" && current) onMark(current, !current.viewed);
      else return;
      event.preventDefault();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  if (error) return <p className="rv2-note" role="alert">{error}</p>;
  if (!diff) return <p className="rv2-empty" role="status">Reading the diff against {header.base}…</p>;
  if (!files.length) return <p className="rv2-empty">No changes between {header.base} and {header.branch}.</p>;
  const toggle = (key: string) => setCollapsed(previous => { const next = new Set(previous); if (next.has(key)) next.delete(key); else next.add(key); return next; });

  return <div className="rv2-files">
    <nav className="rv2-tree" aria-label="Changed files">
      {filter && <div className="rv2-filter"><span>{filter.label}</span><button aria-label="Show all files" onClick={onClearFilter}>✕</button></div>}
      <TreeList nodes={tree} depth={0} collapsed={collapsed} onToggle={toggle} selected={selected} onSelect={setSelected} filter={filter} />
    </nav>
    {current && <div className="rv2-filepane">
      <div className="rv2-filebar">
        <span className="rv2-fname">{current.path}</span>
        <span className="rv2-num"><span className="add">+{current.additions}</span> <span className="del">−{current.deletions}</span></span>
        <span className="rv2-sp" />
        <span className="rv2-seg" role="group" aria-label="Layout">
          <button aria-pressed={layout === "unified"} onClick={() => setLayout("unified")}>Unified</button>
          <button aria-pressed={layout === "split"} onClick={() => setLayout("split")}>Split</button>
        </span>
        <label><input type="checkbox" checked={whitespace} onChange={event => onWhitespace(event.target.checked)} /> Hide whitespace</label>
        <button aria-pressed={current.viewed} onClick={() => onMark(current, !current.viewed)}>{current.viewed ? "Viewed ✓" : "Viewed"} <kbd>v</kbd></button>
        <button onClick={() => step(1)}>Next <kbd>j</kbd></button>
      </div>
      <FileDiff key={`${current.path}:${current.hash}`} file={current} header={header} layout={layout} focusLine={focus?.path === current.path ? focus.line : undefined} onNote={onNote} />
    </div>}
  </div>;
}

function TreeList({ nodes, depth, collapsed, onToggle, selected, onSelect, filter }: {
  nodes: TreeNode[]; depth: number; collapsed: Set<string>; onToggle: (key: string) => void; selected: string | null; onSelect: (path: string) => void; filter: FileFilter | null;
}) {
  return <>{nodes.map(node => {
    if (node.kind === "dir") {
      const shut = collapsed.has(node.key);
      return <div key={node.key}>
        <button className="rv2-dir" style={{ paddingLeft: 12 + depth * 14 }} aria-expanded={!shut} onClick={() => onToggle(node.key)}>
          <span className="tw">{shut ? "▸" : "▾"}</span><span className="nm">{node.name}</span>
          <span className="roll">{node.files} · +{node.additions} −{node.deletions}</span>
        </button>
        {!shut && <TreeList nodes={node.children} depth={depth + 1} collapsed={collapsed} onToggle={onToggle} selected={selected} onSelect={onSelect} filter={filter} />}
      </div>;
    }
    const { file } = node;
    const kind = fileKind(file);
    const out = filter && !file.path.startsWith(filter.prefix);
    return <button key={file.path} className={`rv2-leaf${file.path === selected ? " on" : ""}${file.viewed ? " viewed" : ""}${out ? " out" : ""}`}
      style={{ paddingLeft: 12 + depth * 14 }} title={file.path} aria-current={file.path === selected ? "true" : undefined} onClick={() => onSelect(file.path)}>
      <span className="ck" aria-label={file.viewed ? "Viewed" : undefined}>{file.viewed ? "✓" : ""}</span>
      <span className="nm">{node.name}</span>
      {kind && <span className="kind">{kind}</span>}
      {file.stale && <span className="kind warn">changed</span>}
      <span className="st">+{file.additions} −{file.deletions}</span>
    </button>;
  })}</>;
}

function FileDiff({ file, header, layout, focusLine, onNote }: { file: DiffFile; header: ReviewHeader; layout: Layout; focusLine?: number; onNote: (note: LineNote) => void }) {
  const [sel, setSel] = useState<Selection | null>(null);
  const language = languageFor(file.path);
  const root = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!focusLine) return;
    root.current?.querySelector<HTMLElement>(`[data-line="${focusLine}"]`)?.scrollIntoView({ block: "center" });
  }, [focusLine]);
  function pick(hunk: number, line: number, extend: boolean) {
    setSel(previous => extend && previous && previous.hunk === hunk
      ? { ...previous, from: Math.min(previous.anchor, line), to: Math.max(previous.anchor, line) }
      : { path: file.path, hunk, from: line, to: line, anchor: line });
  }
  if (file.binary) return <p className="rv2-empty">Binary file; nothing to show inline.</p>;
  return <div className="rv2-diff" ref={root}>
    {file.truncated && <p className="rv2-note">Showing the first lines of {file.total_lines}.</p>}
    {file.hunks.map((hunk, index) => <HunkTable key={index} hunk={hunk} index={index} language={language} layout={layout} sel={sel} onPick={pick}
      ask={sel && sel.hunk === index ? <AskBox file={file} hunk={hunk} sel={sel} header={header} onClose={() => setSel(null)} onNote={onNote} /> : null} />)}
    <p className="rv2-hint">Click a line number to ask about it or leave a note. Shift-click for a range. <kbd>j</kbd> <kbd>k</kbd> next and previous file.</p>
  </div>;
}

function HunkTable({ hunk, index, language, layout, sel, onPick, ask }: {
  hunk: DiffHunk; index: number; language: string | null; layout: Layout; sel: Selection | null;
  onPick: (hunk: number, line: number, extend: boolean) => void; ask: React.ReactNode;
}) {
  const mine = !!sel && sel.hunk === index;
  const inSel = (line: number | null) => mine && line !== null && line >= sel!.from && line <= sel!.to;
  // shown: the number printed; line: the number a click selects, or null when
  // this column is not the line's own (an old number beside an added line).
  const num = (shown: number | null, line: number | null) => shown === null ? <td className="n" /> : line === null ? <td className="n">{shown}</td> :
    <td className="n" data-line={line}><button tabIndex={-1} onClick={event => onPick(index, line, event.shiftKey)} aria-label={`Line ${line}`}>{shown}</button></td>;
  const askRow = (key: string) => <tr key={key} className="rv2-askrow"><td colSpan={4}>{ask}</td></tr>;
  const head = <div className="rv2-hh">@@ -{hunk.old_start},{hunk.old_lines} +{hunk.new_start},{hunk.new_lines} @@ {hunk.header}</div>;
  if (layout === "unified") {
    const rows = unifiedRows(hunk);
    const lines = rows.map(row => row.kind === "\\" ? null : rowNumber(row.kind, row.oldNumber, row.newNumber));
    const after = ask ? lines.reduce<number>((at, line, i) => inSel(line) ? i : at, -1) : -1;
    return <div className="rv2-hunk">{head}
      <table className="rv2-table"><colgroup><col className="cn" /><col className="cn" /><col className="cs" /><col /></colgroup><tbody>
        {rows.flatMap((row, i) => {
          const kind = row.kind === "+" ? "add" : row.kind === "-" ? "del" : "";
          const line = lines[i];
          const out = [<tr key={i} className={`${kind}${inSel(line) ? " sel" : ""}`}>
            {num(row.oldNumber, row.kind === "-" ? row.oldNumber : null)}{num(row.newNumber, row.kind === "-" ? null : row.newNumber)}
            <td className="s">{row.kind === "\\" ? "" : row.kind.trim()}</td>
            <td className="c">{row.kind === "\\" ? <code>{row.text.trim()}</code> : <Code text={row.text} language={language} />}</td>
          </tr>];
          if (i === after) out.push(askRow(`ask${i}`));
          return out;
        })}
      </tbody></table>
    </div>;
  }
  const rows = splitRows(hunk);
  const pickOf = (i: number) => rows[i].right?.number ?? (rows[i].left?.kind === "-" ? rows[i].left!.number : null);
  const after = ask ? rows.reduce<number>((at, _, i) => inSel(pickOf(i)) ? i : at, -1) : -1;
  return <div className="rv2-hunk">{head}
    <table className="rv2-table split"><colgroup><col className="cn" /><col /><col className="cn" /><col /></colgroup><tbody>
      {rows.flatMap((row, i) => {
        const { left, right } = row;
        const out = [<tr key={i} className={inSel(pickOf(i)) ? "sel" : ""}>
          {left ? num(left.number, left.kind === "-" && !right ? left.number : null) : <td className="n empty" />}
          <td className={`c ${left ? (left.kind === "-" ? "del" : "") : "empty"}`}>{left && <Code text={left.text} language={language} />}</td>
          {right ? num(right.number, right.number) : <td className="n empty" />}
          <td className={`c ${right ? (right.kind === "+" ? "add" : "") : "empty"}`}>{right && <Code text={right.text} language={language} />}</td>
        </tr>];
        if (i === after) out.push(askRow(`ask${i}`));
        return out;
      })}
    </tbody></table>
  </div>;
}

function AskBox({ file, hunk, sel, header, onClose, onNote }: { file: DiffFile; hunk: DiffHunk; sel: Selection; header: ReviewHeader; onClose: () => void; onNote: (note: LineNote) => void }) {
  const [mode, setMode] = useState<"ask" | "note">("ask");
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [answer, setAnswer] = useState<Answer | null>(null);
  const [saved, setSaved] = useState<string | null>(null);
  const [error, setError] = useState("");
  const input = useRef<HTMLInputElement>(null);
  useEffect(() => { input.current?.focus({ preventScroll: true }); }, [sel.from, sel.to, mode]);
  const label = sel.from === sel.to ? `line ${sel.from}` : `lines ${sel.from}–${sel.to}`;
  const quoted = unifiedRows(hunk).filter(row => row.kind !== "\\").filter(row => {
    const line = rowNumber(row.kind, row.oldNumber, row.newNumber);
    return line >= sel.from && line <= sel.to;
  }).map(row => `${row.kind === " " ? " " : row.kind}${row.text}`).join("\n");
  async function submit() {
    const value = text.trim();
    if (mode === "note") {
      if (!value) return;
      onNote({ path: file.path, from: sel.from, to: sel.to, text: value });
      setSaved(value); setText("");
      return;
    }
    const question = value || `What does ${label} do, and why did it change?`;
    setBusy(true); setError("");
    try {
      const result = await api.reviewAsk(header.repo, header.ref, { question, hunk: `${file.path}:${sel.from}${sel.to !== sel.from ? `-${sel.to}` : ""}\n${quoted}` });
      setAnswer({ q: question, a: result.a, citations: result.citations }); setText("");
    } catch (cause) { setError((cause as Error).message); }
    finally { setBusy(false); }
  }
  return <div className="rv2-ask" onKeyDown={event => { if (event.key === "Escape") { event.stopPropagation(); onClose(); } }}>
    <div className="rv2-ask-head">
      <span className="lbl">{label} · <code>{quoted.replace(/\s+/g, " ").trim().slice(0, 70)}</code></span>
      <button className="x" aria-label="Close" onClick={onClose}>✕</button>
    </div>
    <span className="rv2-seg" role="group" aria-label="Ask or note">
      <button aria-pressed={mode === "ask"} onClick={() => setMode("ask")}>Ask</button>
      <button aria-pressed={mode === "note"} onClick={() => setMode("note")}>Note for the author</button>
    </span>
    {answer && <div className="rv2-answer"><span className="q">{answer.q}</span><div dangerouslySetInnerHTML={{ __html: modelHtml(answer.a) }} />
      {answer.citations.length > 0 && <span className="src">{answer.citations.map(c => `${c.path}:${c.line}`).join(" · ")}</span>}</div>}
    {saved && <div className="rv2-answer mine">{saved}<span className="src">Saved to your notes · sent with Request changes</span></div>}
    {error && <p className="rv2-note" role="alert">{error}</p>}
    <form onSubmit={event => { event.preventDefault(); void submit(); }}>
      <input ref={input} value={text} disabled={busy} onChange={event => setText(event.target.value)}
        placeholder={mode === "ask" ? `Ask about ${label}…` : "Write a note the author will see…"} aria-label={mode === "ask" ? "Question about the selected lines" : "Note for the author"} />
      <button className="primary" disabled={busy || (mode === "note" && !text.trim())}>{busy ? "Reading…" : mode === "ask" ? "Ask" : "Add note"}</button>
    </form>
  </div>;
}
