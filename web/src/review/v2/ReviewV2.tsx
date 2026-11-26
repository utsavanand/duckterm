import { useCallback, useEffect, useRef, useState } from "react";
import { api, type DiffFile, type ReviewCards, type ReviewDiff, type ReviewHeader } from "../../api";
import { Modal } from "../../ui";
import { ApproveDialog } from "../ApproveDialog";
import { ChatPanel } from "../ChatPanel";
import { HistoryPane } from "../HistoryPane";
import { shortSha } from "../diffModel";
import { ChangesTab, MapTab, ReadinessTab } from "./Boards";
import { FilesTab, type FileFilter, type LineNote } from "./FilesTab";
import { overall, readinessRows } from "./readiness";
import "./v2.css";

type Tab = "map" | "changes" | "files" | "ready" | "history";
const POLL_MS = 3000;

function notesKey(repo: string, ref: string) { return `rv2-notes:${repo}:${ref}`; }
function loadNotes(repo: string, ref: string): LineNote[] {
  try { return JSON.parse(localStorage.getItem(notesKey(repo, ref)) ?? "[]") as LineNote[]; } catch { return []; }
}

export function ReviewV2({ repo, target, from, onBack }: { repo: string; target: string; from?: string; onBack: () => void }) {
  const [header, setHeader] = useState<ReviewHeader | null>(null);
  const [error, setError] = useState("");
  const [tab, setTab] = useState<Tab>("map");
  const [chat, setChat] = useState(false);
  const [diff, setDiff] = useState<ReviewDiff | null>(null);
  const [diffError, setDiffError] = useState("");
  const [whitespace, setWhitespace] = useState(true);
  const [cards, setCards] = useState<ReviewCards | null>(null);
  const [filter, setFilter] = useState<FileFilter | null>(null);
  const [focus, setFocus] = useState<{ path: string; line?: number; nonce: number } | null>(null);
  const [notes, setNotes] = useState<LineNote[]>(() => loadNotes(repo, target));
  const [requesting, setRequesting] = useState<string | null>(null);
  const [approving, setApproving] = useState(false);
  const [status, setStatus] = useState("");
  const alive = useRef(true);
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);

  const loadHeader = useCallback(async (refresh: boolean) => {
    try { const result = await api.reviewHeader(repo, target, refresh); if (alive.current) { setHeader(result); setError(""); } }
    catch (cause) { if (alive.current) setError((cause as Error).message); }
  }, [repo, target]);
  useEffect(() => { void loadHeader(false); }, [loadHeader]);
  const ref = header?.ref;
  useEffect(() => {
    if (!header) return;
    void api.reviewDiff(repo, header.ref, { whitespace, since: header.previous_head })
      .then(result => { if (alive.current) { setDiff(result); setDiffError(""); } })
      .catch((cause: Error) => { if (alive.current) setDiffError(cause.message); });
  }, [repo, header, whitespace]);
  const loadCards = useCallback(async () => {
    if (!ref) return;
    try { const result = await api.reviewCards(repo, ref); if (alive.current) setCards(result); } catch { /* the boards show "not read yet" */ }
  }, [repo, ref]);
  useEffect(() => { void loadCards(); }, [loadCards]);
  const generating = !!cards && (cards.running || cards.cards.some(card => card.status === "pending" || card.status === "stale"));
  useEffect(() => {
    if (!generating) return;
    const timer = setInterval(() => void loadCards(), POLL_MS);
    return () => clearInterval(timer);
  }, [generating, loadCards]);
  useEffect(() => { try { localStorage.setItem(notesKey(repo, target), JSON.stringify(notes)); } catch { /* notes stay for this visit */ } }, [repo, target, notes]);
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      const el = event.target as HTMLElement | null;
      if (el && ["INPUT", "TEXTAREA", "SELECT"].includes(el.tagName)) return;
      if (event.key === "c" && !event.metaKey && !event.ctrlKey) { event.preventDefault(); setChat(open => !open); }
      if (event.key === "Escape") setChat(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  async function generate() {
    if (!ref) return;
    try { setCards(await api.regenerateCards(repo, ref)); } catch (cause) { setStatus((cause as Error).message); }
  }
  async function mark(file: DiffFile, viewed: boolean) {
    if (!ref) return;
    setDiff(previous => previous && { ...previous, files: previous.files.map(f => f.path === file.path ? { ...f, viewed, stale: false } : f), counts: { ...previous.counts, viewed: previous.counts.viewed + (viewed ? 1 : -1) } });
    try { await api.markViewed(repo, ref, { path: file.path, hash: file.hash, viewed }); } catch (cause) { setStatus((cause as Error).message); }
  }
  const openFile = useCallback((path: string, line?: number) => { setFocus({ path, line, nonce: Date.now() }); setFilter(null); setTab("files"); }, []);
  async function postRequest(body: string) {
    if (!ref) return;
    try {
      const result = await api.reviewGithub(repo, ref, { event: "REQUEST_CHANGES", body });
      setRequesting(null); setNotes([]); setStatus(`Changes requested on GitHub${result.url ? "" : ""}.`); void loadHeader(true);
    } catch (cause) { setStatus((cause as Error).message); }
  }

  const pr = header?.pr ?? null;
  const row = header?.row ?? null;
  const list = cards?.cards ?? [];
  const ready = header ? overall(readinessRows(header, list)) : null;
  const canReview = !!(pr && header?.github && pr.state === "open");
  const tabs: { id: Tab; label: string; extra?: string; tone?: string }[] = [
    { id: "map", label: "Map" }, { id: "changes", label: "Changes" },
    { id: "files", label: "Files", extra: diff ? `${diff.counts.viewed}/${diff.files.length}` : undefined },
    { id: "ready", label: "Release readiness", extra: ready && ready.status !== "wait" ? (ready.status === "block" ? "not ready" : ready.status === "part" ? "open items" : "ready") : undefined, tone: ready?.status },
    { id: "history", label: "History" },
  ];
  const noteBody = notes.map(note => `**${note.path}:${note.from}${note.to !== note.from ? `-${note.to}` : ""}**\n${note.text}`).join("\n\n");

  return <section className="rv2" aria-label="Review workspace">
    <header className="rv2-top">
      <button className="rv2-back" onClick={onBack}>← Pull requests</button>
      <div className="rv2-title">
        <h2>{pr ? `#${pr.number} ${pr.title}` : header?.branch ?? target}</h2>
        {header && <p className="rv2-meta">
          <code>{header.branch} → {header.base}</code>
          <span>{header.commits} {header.commits === 1 ? "commit" : "commits"}{row?.size ? ` · ${row.size.files} files` : ""}</span>
          {row?.size && <span><span className="add">+{row.size.additions}</span> <span className="del">−{row.size.deletions}</span></span>}
          {row && <span className={`rv2-tag${row.status === "conflict" || row.status === "ci_failing" ? " bad" : ""}`}>{row.label}</span>}
          {(header.session?.name ?? from) && <span>from {header.session?.name ?? from}</span>}
          <span>head {shortSha(header.head_sha)}</span>
        </p>}
        {error && <p className="rv2-note" role="alert">{error}</p>}
        {status && <p className="rv2-note" role="status">{status}</p>}
      </div>
      <div className="rv2-actions">
        {header?.github && <a href={pr ? pr.url : `https://github.com/${header.github}/tree/${header.branch}`} target="_blank" rel="noreferrer">Open on GitHub</a>}
        {canReview && <button onClick={() => setRequesting(noteBody)}>Request changes{notes.length ? ` · ${notes.length} ${notes.length === 1 ? "note" : "notes"}` : ""}</button>}
        {canReview && pr && <button className="primary" onClick={() => setApproving(true)}>Approve…</button>}
      </div>
    </header>
    <div className="rv2-tabs" role="tablist" aria-label="Review views">
      {tabs.map(item => <button key={item.id} role="tab" id={`rv2-${item.id}-tab`} aria-selected={tab === item.id} aria-controls={`rv2-${item.id}`} onClick={() => setTab(item.id)}>
        {item.label}{item.extra && <small className={item.tone ? `is-${item.tone}` : undefined}>{item.extra}</small>}
      </button>)}
    </div>
    <div className="rv2-body">
      <div className="rv2-main">
        {!header && !error && <p className="rv2-empty" role="status">Loading {target}…</p>}
        {header && <>
          <div id="rv2-map" role="tabpanel" aria-labelledby="rv2-map-tab" hidden={tab !== "map"} className="rv2-panel scroll">
            <MapTab header={header} diff={diff} cards={list} onGenerate={() => void generate()} generating={generating} onArea={next => { setFilter(next); setTab("files"); }} />
          </div>
          <div id="rv2-changes" role="tabpanel" aria-labelledby="rv2-changes-tab" hidden={tab !== "changes"} className="rv2-panel scroll"><ChangesTab cards={list} /></div>
          <div id="rv2-files" role="tabpanel" aria-labelledby="rv2-files-tab" hidden={tab !== "files"} className="rv2-panel">
            <FilesTab header={header} diff={diff} error={diffError} whitespace={whitespace} onWhitespace={setWhitespace} focus={focus}
              filter={filter} onClearFilter={() => setFilter(null)} onMark={(file, viewed) => void mark(file, viewed)} onNote={note => setNotes(previous => [...previous, note])} />
          </div>
          <div id="rv2-ready" role="tabpanel" aria-labelledby="rv2-ready-tab" hidden={tab !== "ready"} className="rv2-panel scroll">
            <ReadinessTab header={header} cards={list} onGenerate={() => void generate()} generating={generating} onSent={() => void loadCards()} />
          </div>
          <div id="rv2-history" role="tabpanel" aria-labelledby="rv2-history-tab" hidden={tab !== "history"} className="rv2-panel scroll">
            {tab === "history" && <HistoryPane header={header} active />}
          </div>
        </>}
      </div>
      {chat && header ? <aside className="rv2-chat" aria-label="Review chat">
        <ChatPanel header={header} quote="" onClearQuote={() => undefined} onOpenFile={openFile} onDraft={body => setRequesting(body)}
          onCollapse={() => setChat(false)} pendingQuestion="" onQuestionTaken={() => undefined} />
      </aside> : <button className="rv2-chatrail" onClick={() => setChat(true)} title="Open chat (c)">Chat · <kbd>c</kbd></button>}
    </div>
    {approving && pr && <ApproveDialog repo={repo} target={target} prNumber={pr.number} onClose={() => setApproving(false)} onApproved={() => void loadHeader(true)} />}
    {requesting !== null && <Modal title="Request changes on GitHub" onClose={() => setRequesting(null)}>
      <form className="rv2-fixform" onSubmit={event => { event.preventDefault(); void postRequest(requesting); }}>
        <p className="rv2-dim">Posts a request-changes review on {pr ? `#${pr.number}` : "this PR"} as you{notes.length ? `, with your ${notes.length} line ${notes.length === 1 ? "note" : "notes"}` : ""}. Nothing is sent until you click Post.</p>
        <textarea rows={12} aria-label="Review body" value={requesting} onChange={event => setRequesting(event.target.value)} />
        <div className="rv2-fixactions">
          <button type="button" onClick={() => setRequesting(null)}>Cancel</button>
          <button className="primary" disabled={!requesting.trim()}>Post as review</button>
        </div>
      </form>
    </Modal>}
  </section>;
}
