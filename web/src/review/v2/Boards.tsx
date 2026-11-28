import { useMemo, useState } from "react";
import type { ReviewCard, ReviewDiff, ReviewHeader } from "../../api";
import type { DesignData, IntentData, ReleaseData, TestsData } from "../cardModel";
import { shortSha } from "../diffModel";
import { ChangesBoard, DesignHistory, MapDiagram } from "./Design";
import { overall, readinessRows, STATUS_LABELS, type ReadyRow } from "./readiness";
import { fileKind } from "./tree";
import type { FileFilter } from "./FilesTab";

// A row shows its first few lines; the fix request always carries them all.
const SHOWN = 4;

export function cardData<T>(cards: ReviewCard[], id: string): T | null {
  const card = cards.find(item => item.id === id);
  return card?.status === "ready" && card.data ? card.data as unknown as T : null;
}

// Names for the rule paths a PR touches, from the release card's checks.
const TOUCHED: Record<string, string> = { "Security or credential path": "Credentials", "Schema or data path": "Stored data", "Installer, updater or build path": "Installer or build" };

// Until the design card is written, areas are the first two path segments.
function areas(diff: ReviewDiff | null): { label: string; files: string[]; additions: number; deletions: number; added: boolean }[] {
  const map = new Map<string, { label: string; files: string[]; additions: number; deletions: number; added: boolean }>();
  for (const file of diff?.files ?? []) {
    const parts = file.path.split("/");
    const label = parts.length > 2 ? `${parts.slice(0, 2).join("/")}/` : parts.length === 2 ? `${parts[0]}/` : "(top level)";
    const entry = map.get(label) ?? { label, files: [], additions: 0, deletions: 0, added: true };
    entry.files.push(file.path); entry.additions += file.additions; entry.deletions += file.deletions; entry.added = entry.added && file.status === "added";
    map.set(label, entry);
  }
  return [...map.values()].sort((a, b) => (b.additions + b.deletions) - (a.additions + a.deletions));
}

function Waiting({ card, cards, generating, onGenerate, what }: { card: string; cards: ReviewCard[]; generating: boolean; onGenerate: () => void; what: string }) {
  const state = cards.find(item => item.id === card);
  if (generating || state?.status === "pending" || state?.status === "stale") return <p className="rv2-dim" role="status">Reading the diff for {what}…</p>;
  if (state?.status === "omitted" || state?.status === "failed") return <p className="rv2-note">{what[0].toUpperCase() + what.slice(1)} could not be written: {state.reason ?? "no result"}. <button onClick={onGenerate}>Try again</button></p>;
  return <p className="rv2-dim">Not read yet. <button className="primary" onClick={onGenerate}>Read this PR</button></p>;
}

export function MapTab({ header, diff, cards, onGenerate, generating, onFiles }: {
  header: ReviewHeader; diff: ReviewDiff | null; cards: ReviewCard[]; onGenerate: () => void; generating: boolean; onFiles: (filter: FileFilter) => void;
}) {
  const summary = cardData<IntentData>(cards, "intent");
  const design = cardData<DesignData>(cards, "design");
  const release = cardData<ReleaseData>(cards, "release");
  const tests = cardData<TestsData>(cards, "tests");
  const kinds = useMemo(() => {
    const counts = { code: 0, test: 0, gen: 0 };
    for (const file of diff?.files ?? []) { const kind = fileKind(file); if (kind === "test") counts.test += 1; else if (kind === "gen") counts.gen += 1; else counts.code += 1; }
    return counts;
  }, [diff]);
  const untested = useMemo(() => new Set(tests?.coverage.filter(row => row.covered === "no").map(row => row.path) ?? []), [tests]);
  const row = header.row;
  const unstated = summary?.claims.filter(claim => claim.in_diff === "false") ?? [];
  const touched = release?.checks.filter(check => TOUCHED[check.name] && check.hits.length).map(check => TOUCHED[check.name]) ?? [];
  const moved = release?.base_moved;
  const first = release?.commits?.[0];
  const list = areas(diff);
  const most = Math.max(1, ...list.map(area => area.additions + area.deletions));
  return <div className="rv2-board">
    <div className="rv2-lede">
      <div>
        <h3 className="rv2-lbl">What this PR does <span className="rv2-ai">AI summary of the diff</span></h3>
        {summary ? <p className="rv2-summary">{summary.summary.split("\n\n")[0]}</p> : <Waiting card="intent" cards={cards} generating={generating} onGenerate={onGenerate} what="the summary" />}
      </div>
      <div>
        <h3 className="rv2-lbl">Facts</h3>
        <dl className="rv2-facts">
          <dt>Size</dt><dd>{diff ? `${diff.files.length} files: ${kinds.code} code, ${kinds.test} tests, ${kinds.gen} generated` : "…"}</dd>
          <dt>Commits</dt><dd>{header.commits} {header.commits === 1 ? "commit" : "commits"} · head <code>{shortSha(header.head_sha)}</code>{header.commits === 1 && first ? ` ${first.subject}` : ""}</dd>
          <dt>Based on</dt><dd><code>{header.base} {shortSha(header.base_sha)}</code>{moved && moved.commits > 0 && <> · <span className="warn">{header.base} has {moved.commits} {moved.commits === 1 ? "commit" : "commits"} since{moved.overlap.length ? `, ${moved.overlap.length} touching this PR's files` : ""}</span></>}</dd>
          {summary && <><dt>Description</dt><dd className={unstated.length ? "warn" : ""}>{unstated.length ? `Claims ${unstated.map(claim => `"${claim.claim}"`).join(", ").slice(0, 160)}. Not in the diff.` : "Matches the diff"}</dd></>}
          {release && <><dt>Paths touched</dt><dd className={touched.length ? "warn" : ""}>{touched.length ? touched.join(" · ") : "None of the guarded paths"}</dd></>}
          <dt>Merge</dt><dd className={row?.status === "conflict" ? "bad" : ""}>{row?.status === "conflict" ? `Conflicts with ${header.base}` : row?.label ?? "—"}</dd>
          <dt>CI</dt><dd className={row?.checks?.failed ? "bad" : ""}>{!row?.checks || row.checks.total === 0 ? "No checks ran on this head" : row.checks.failed ? `${row.checks.failed} of ${row.checks.total} failing` : row.checks.pending ? `${row.checks.pending} running` : `${row.checks.passed}/${row.checks.total} passed`}</dd>
        </dl>
      </div>
    </div>
    <div>
      <h3 className="rv2-lbl">Where it lands · click a box to open its files</h3>
      {design ? <MapDiagram design={design} untested={untested} onFiles={onFiles} /> : <>
        <Waiting card="design" cards={cards} generating={generating} onGenerate={onGenerate} what="the map" />
        <ul className="rv2-areas">{list.map(area => <li key={area.label}>
          <button onClick={() => onFiles({ label: area.label, files: area.files })}>
            <span className={`nm${area.added ? " new" : ""}`}>{area.label}</span>
            <span className="bar"><i className="a" style={{ width: `${area.additions / most * 100}%` }} /><i className="d" style={{ width: `${area.deletions / most * 100}%` }} /></span>
            <span className="st">{area.files.length} · <span className="add">+{area.additions}</span> <span className="del">−{area.deletions}</span></span>
          </button>
        </li>)}</ul>
      </>}
    </div>
  </div>;
}

export function ChangesTab({ cards, generating, onGenerate, onFiles }: { cards: ReviewCard[]; generating: boolean; onGenerate: () => void; onFiles: (filter: FileFilter) => void }) {
  const design = cardData<DesignData>(cards, "design");
  return <div className="rv2-board">
    <h3 className="rv2-lbl">What changed, as problem → solution <span className="rv2-ai">written by AI from the diff</span></h3>
    {design ? <ChangesBoard design={design} onFiles={onFiles} /> : <Waiting card="design" cards={cards} generating={generating} onGenerate={onGenerate} what="the changes" />}
  </div>;
}

export function HistoryTab({ header, cards, generating, onGenerate }: { header: ReviewHeader; cards: ReviewCard[]; generating: boolean; onGenerate: () => void }) {
  const design = cardData<DesignData>(cards, "design");
  return <div className="rv2-board">
    {design?.boxes.length ? <DesignHistory design={design} base={`${header.base} ${shortSha(header.base_sha)}`} head={shortSha(header.head_sha)} />
      : design ? <p className="rv2-dim">The model drew no design for this diff.</p>
      : <><h3 className="rv2-lbl">Design of the service under review</h3><Waiting card="design" cards={cards} generating={generating} onGenerate={onGenerate} what="the design" /></>}
  </div>;
}

export function ReadinessTab({ header, cards, onGenerate, generating, onDraft, sent }: {
  header: ReviewHeader; cards: ReviewCard[]; onGenerate: () => void; generating: boolean; onDraft: (row: ReadyRow) => void; sent: Record<string, string>;
}) {
  const rows = readinessRows(header, cards);
  const total = overall(rows);
  const [open, setOpen] = useState<Set<string>>(new Set());
  const author = header.session?.name ?? null;
  return <div className="rv2-board">
    <div className="rv2-readyhead">
      <span className={`rv2-big is-${total.status}`}>{total.label}</span>
      <span className="rv2-dim">{total.counts.block} blocking · {total.counts.part} partial · {total.counts.ok} ready{total.counts.wait ? ` · ${total.counts.wait} not read yet` : ""}</span>
      {total.counts.wait > 0 && !generating && <button className="primary" onClick={onGenerate}>Read this PR</button>}
    </div>
    <div className="rv2-rr">{rows.map(row => <div key={row.id} className="rv2-rrow">
      <span className="nm">{row.name}<small>{row.sub}</small></span>
      <span className={`rv2-st is-${row.status}`}>{STATUS_LABELS[row.status]}</span>
      <div className="bd">
        {(open.has(row.id) ? row.lines : row.lines.slice(0, SHOWN)).map((line, i) => <div key={i} className="ln"><span>{line.text}</span>{line.where && <span className="w">{line.where}</span>}</div>)}
        {row.lines.length > SHOWN && <button className="rv2-more" onClick={() => setOpen(previous => { const next = new Set(previous); if (next.has(row.id)) next.delete(row.id); else next.add(row.id); return next; })}>
          {open.has(row.id) ? "Show fewer" : `Show ${row.lines.length - SHOWN} more`}</button>}
        {row.ask && (sent[row.id] ? <span className="rv2-sent">{sent[row.id]}</span>
          : <button className="rv2-fix" onClick={() => onDraft(row)}>{author ? `Ask ${author} to fix →` : "Comment on the PR →"}</button>)}
      </div>
    </div>)}</div>
  </div>;
}
