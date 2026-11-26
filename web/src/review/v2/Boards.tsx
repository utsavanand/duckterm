import { useMemo, useState } from "react";
import { api, type ReviewCard, type ReviewDiff, type ReviewHeader } from "../../api";
import { Modal } from "../../ui";
import type { IntentData } from "../cardModel";
import { shortSha } from "../diffModel";
import { overall, readinessRows, STATUS_LABELS, type ReadyRow } from "./readiness";
import { fileKind } from "./tree";
import type { FileFilter } from "./FilesTab";

// A row shows its first few lines; the fix request always carries them all.
const SHOWN = 4;

function intent(cards: ReviewCard[]): IntentData | null {
  const card = cards.find(item => item.id === "intent");
  return card?.status === "ready" && card.data ? card.data as unknown as IntentData : null;
}

// Areas are the first two path segments ("backend/entourage" → its first
// level below); a PR touching one folder still splits into what it touched.
function areas(diff: ReviewDiff | null): { prefix: string; files: number; additions: number; deletions: number; added: boolean }[] {
  const map = new Map<string, { prefix: string; files: number; additions: number; deletions: number; added: boolean }>();
  for (const file of diff?.files ?? []) {
    const parts = file.path.split("/");
    const prefix = parts.length > 2 ? `${parts.slice(0, 2).join("/")}/` : parts.length === 2 ? `${parts[0]}/` : "";
    const entry = map.get(prefix) ?? { prefix, files: 0, additions: 0, deletions: 0, added: true };
    entry.files += 1; entry.additions += file.additions; entry.deletions += file.deletions; entry.added = entry.added && file.status === "added";
    map.set(prefix, entry);
  }
  return [...map.values()].sort((a, b) => (b.additions + b.deletions) - (a.additions + a.deletions));
}

export function MapTab({ header, diff, cards, onGenerate, generating, onArea }: {
  header: ReviewHeader; diff: ReviewDiff | null; cards: ReviewCard[]; onGenerate: () => void; generating: boolean; onArea: (filter: FileFilter) => void;
}) {
  const summary = intent(cards);
  const kinds = useMemo(() => {
    const counts = { code: 0, test: 0, gen: 0 };
    for (const file of diff?.files ?? []) { const kind = fileKind(file); if (kind === "test") counts.test += 1; else if (kind === "gen") counts.gen += 1; else counts.code += 1; }
    return counts;
  }, [diff]);
  const list = areas(diff);
  const most = Math.max(1, ...list.map(area => area.additions + area.deletions));
  const row = header.row;
  const unstated = summary?.claims.filter(claim => claim.in_diff === "false").length ?? 0;
  return <div className="rv2-board">
    <div className="rv2-lede">
      <div>
        <h3 className="rv2-lbl">What this PR does <span className="rv2-ai">AI summary of the diff</span></h3>
        {summary ? <p className="rv2-summary">{summary.summary.split("\n\n")[0]}</p>
          : <p className="rv2-dim">{generating ? "Reading the diff…" : <>Not read yet. <button className="primary" onClick={onGenerate}>Read this PR</button></>}</p>}
      </div>
      <div>
        <h3 className="rv2-lbl">Facts</h3>
        <dl className="rv2-facts">
          <dt>Size</dt><dd>{diff ? `${diff.files.length} files: ${kinds.code} code, ${kinds.test} tests, ${kinds.gen} generated` : "…"}</dd>
          <dt>Commits</dt><dd>{header.commits} · head <code>{shortSha(header.head_sha)}</code></dd>
          <dt>Based on</dt><dd><code>{header.base} {shortSha(header.base_sha)}</code></dd>
          {summary && <><dt>Description</dt><dd className={unstated ? "warn" : ""}>{unstated ? `${unstated} ${unstated === 1 ? "claim is" : "claims are"} not in the diff` : "Matches the diff"}</dd></>}
          <dt>Merge</dt><dd className={row?.status === "conflict" ? "bad" : ""}>{row?.status === "conflict" ? `Conflicts with ${header.base}` : row?.label ?? "—"}</dd>
          <dt>CI</dt><dd className={row?.checks?.failed ? "bad" : ""}>{!row?.checks || row.checks.total === 0 ? "No checks ran on this head" : row.checks.failed ? `${row.checks.failed} of ${row.checks.total} failing` : row.checks.pending ? `${row.checks.pending} running` : `${row.checks.passed}/${row.checks.total} passed`}</dd>
        </dl>
      </div>
    </div>
    <div>
      <h3 className="rv2-lbl">Where it lands · click an area to review its files</h3>
      <ul className="rv2-areas">{list.map(area => <li key={area.prefix}>
        <button onClick={() => onArea({ label: area.prefix || "top level", prefix: area.prefix })}>
          <span className={`nm${area.added ? " new" : ""}`}>{area.prefix || "(top level)"}</span>
          <span className="bar"><i className="a" style={{ width: `${area.additions / most * 100}%` }} /><i className="d" style={{ width: `${area.deletions / most * 100}%` }} /></span>
          <span className="st">{area.files} · <span className="add">+{area.additions}</span> <span className="del">−{area.deletions}</span></span>
        </button>
      </li>)}</ul>
    </div>
  </div>;
}

export function ChangesTab({ cards }: { cards: ReviewCard[] }) {
  const summary = intent(cards);
  return <div className="rv2-board">
    <h3 className="rv2-lbl">What changed, as problem → solution</h3>
    <p className="rv2-dim">This list is written in the next update of the review page. Until then, the PR's claims and what the diff shows:</p>
    {summary ? <ul className="rv2-items">{summary.claims.map((claim, i) => <li key={i}>
      <span className={`rv2-tag${claim.in_diff === "false" ? " bad" : claim.in_diff === "partial" ? " warn" : ""}`}>{claim.in_diff === "true" ? "in diff" : claim.in_diff === "false" ? "not in diff" : "partly"}</span>
      <span>{claim.claim}</span>
    </li>)}</ul> : <p className="rv2-dim">Not read yet.</p>}
  </div>;
}

export function ReadinessTab({ header, cards, onGenerate, generating, onSent }: {
  header: ReviewHeader; cards: ReviewCard[]; onGenerate: () => void; generating: boolean; onSent: () => void;
}) {
  const rows = readinessRows(header, cards);
  const total = overall(rows);
  const [drafting, setDrafting] = useState<ReadyRow | null>(null);
  const [sent, setSent] = useState<Record<string, string>>({});
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
          : <button className="rv2-fix" onClick={() => setDrafting(row)}>{author ? `Ask ${author} to fix →` : "Comment on the PR →"}</button>)}
      </div>
    </div>)}</div>
    {drafting && <FixDialog header={header} row={drafting} onClose={() => setDrafting(null)} onSent={label => { setSent(previous => ({ ...previous, [drafting.id]: label })); setDrafting(null); onSent(); }} />}
  </div>;
}

function FixDialog({ header, row, onClose, onSent }: { header: ReviewHeader; row: ReadyRow; onClose: () => void; onSent: (label: string) => void }) {
  const canInbox = !!header.session;
  const canGithub = !!(header.pr && header.github);
  const [dest, setDest] = useState<"inbox" | "github">(canInbox ? "inbox" : "github");
  const [text, setText] = useState(row.ask ?? "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  async function send() {
    setBusy(true); setError("");
    try {
      if (dest === "inbox") {
        await api.reviewSend(header.repo, header.ref, { card: row.card, items: [text.trim()], session: header.session!.key });
        onSent(`Sent to ${header.session!.name} · waiting`);
      } else {
        await api.reviewGithub(header.repo, header.ref, { event: "COMMENT", body: text.trim() });
        onSent(`Commented on #${header.pr!.number}`);
      }
    } catch (cause) { setError((cause as Error).message); }
    finally { setBusy(false); }
  }
  return <Modal title={`Fix request · ${row.name}`} onClose={onClose}>
    <form className="rv2-fixform" onSubmit={event => { event.preventDefault(); void send(); }}>
      <span className="rv2-seg" role="group" aria-label="Send to">
        <button type="button" aria-pressed={dest === "inbox"} disabled={!canInbox} onClick={() => setDest("inbox")}>{header.session ? `${header.session.name}'s inbox` : "No authoring session"}</button>
        <button type="button" aria-pressed={dest === "github"} disabled={!canGithub} onClick={() => setDest("github")}>GitHub comment</button>
      </span>
      <p className="rv2-dim">{dest === "inbox" ? `Goes to ${header.session?.name}, the session that opened this PR.` : `Posted on #${header.pr?.number} on GitHub as you.`}</p>
      <textarea rows={9} aria-label="Fix request" value={text} onChange={event => setText(event.target.value)} />
      {error && <p className="rv2-note" role="alert">{error}</p>}
      <div className="rv2-fixactions">
        <button type="button" onClick={onClose}>Cancel</button>
        <button className="primary" disabled={busy || !text.trim()}>{busy ? "Sending…" : dest === "inbox" ? `Send to ${header.session?.name}` : "Post comment"}</button>
      </div>
    </form>
  </Modal>;
}
