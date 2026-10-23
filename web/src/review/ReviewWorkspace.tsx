import { useCallback, useEffect, useRef, useState } from "react";
import { api, type ReviewHeader } from "../api";
import { Modal } from "../ui";
import { CardsPane } from "./CardsPane";
import { ChatPanel } from "./ChatPanel";
import { DiffPane, type DiffFocus } from "./DiffPane";
import { shortSha } from "./diffModel";
import "./review.css";

type Tab = "diff" | "review" | "history";
const TABS: { id: Tab; label: string }[] = [{ id: "diff", label: "Diff" }, { id: "review", label: "Review" }, { id: "history", label: "History" }];
type ReviewEvent = "APPROVE" | "REQUEST_CHANGES";

export function ReviewWorkspace({ repo, target, from, onBack }: { repo: string; target: string; from?: string; onBack: () => void }) {
  const [header, setHeader] = useState<ReviewHeader | null>(null);
  const [error, setError] = useState("");
  const [tab, setTab] = useState<Tab>("diff");
  const [chat, setChat] = useState(() => window.innerWidth >= 1000);
  const [quote, setQuote] = useState("");
  const [pendingQuestion, setPendingQuestion] = useState("");
  const [focus, setFocus] = useState<DiffFocus | null>(null);
  const [dialog, setDialog] = useState<{ event: ReviewEvent; body: string } | null>(null);
  const [posting, setPosting] = useState(false);
  const [posted, setPosted] = useState<string | null>(null);
  const [dialogError, setDialogError] = useState("");
  const alive = useRef(true);
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);
  const load = useCallback(async (refresh: boolean) => {
    try {
      const result = await api.reviewHeader(repo, target, refresh);
      if (alive.current) { setHeader(result); setError(""); }
    } catch (cause) {
      if (alive.current) setError((cause as Error).message);
    }
  }, [repo, target]);
  useEffect(() => { void load(false); }, [load]);
  const pr = header?.pr ?? null;
  const row = header?.row ?? null;
  const title = pr ? `#${pr.number} ${pr.title}` : header?.branch ?? target;
  const github = header?.github ? (pr ? pr.url : `https://github.com/${header.github}/tree/${header.branch}`) : null;
  const canReview = !!(pr && header?.github && pr.state === "open");
  const ask = useCallback((text: string) => { setQuote(text); setChat(true); }, []);
  const askQuestion = useCallback((text: string) => { setPendingQuestion(text); setChat(true); }, []);
  const clearQuote = useCallback(() => setQuote(""), []);
  const questionTaken = useCallback(() => setPendingQuestion(""), []);
  const openFile = useCallback((path: string, hunk?: number, line?: number) => { setFocus({ path, hunk, line, nonce: Date.now() }); setTab("diff"); }, []);
  async function post() {
    if (!dialog) return;
    setPosting(true); setDialogError("");
    try {
      const result = await api.reviewGithub(repo, target, { event: dialog.event, body: dialog.body });
      setPosted(result.url ?? "posted");
      setDialog(null);
      void load(true);
    } catch (cause) { setDialogError((cause as Error).message); }
    finally { setPosting(false); }
  }
  return <section className={`rd-review${chat ? " chat-open" : ""}`} aria-label="Review workspace">
    <header className="rd-review-head">
      <button className="rd-review-back" onClick={onBack}>← Pull requests</button>
      <div className="rd-review-title">
        <h2>{title}</h2>
        {header && <p>
          <code>{header.branch}</code> → <code>{header.base}</code>
          {` · ${header.commits} ${header.commits === 1 ? "commit" : "commits"}`}
          {row?.size ? ` · ${row.size.files} ${row.size.files === 1 ? "file" : "files"} ` : " "}
          {row?.size && <><span className="add">+{row.size.additions}</span> <span className="del">−{row.size.deletions}</span></>}
          {row && <> · <span className={`rd-pr-status is-${row.status}`}>{row.label}</span></>}
          {row?.checks && row.checks.total > 0 && ` · checks ${row.checks.failed ? `${row.checks.failed} failed` : row.checks.pending ? `${row.checks.pending} running` : "passing"}`}
          {(header.session?.name ?? from) && ` · from ${header.session?.name ?? from}`}
          {` · head ${shortSha(header.head_sha)}`}
        </p>}
        {error && <p className="rd-prs-note" role="alert">{error}</p>}
        {posted && <p className="rd-prs-note" role="status">Review posted to GitHub{posted.startsWith("http") ? <>: <a href={posted} target="_blank" rel="noreferrer">open it</a></> : ""}.</p>}
      </div>
      <div className="rd-review-actions">
        <button className="rd-btn rd-btn-ghost rd-btn-sm" aria-pressed={chat} onClick={() => setChat(open => !open)}>Chat</button>
        {github && <a className="rd-prs-link" href={github} target="_blank" rel="noreferrer">Open on GitHub</a>}
        {canReview && <button className="rd-btn rd-btn-ghost rd-btn-sm" onClick={() => setDialog({ event: "REQUEST_CHANGES", body: "" })}>Request changes</button>}
        {canReview && <button className="rd-btn rd-btn-primary rd-btn-sm" onClick={() => setDialog({ event: "APPROVE", body: "" })}>Approve</button>}
      </div>
    </header>
    <div className="rd-review-tabs" role="tablist" aria-label="Review views">
      {TABS.map(item => <button key={item.id} id={`review-${item.id}-tab`} role="tab" aria-selected={tab === item.id} aria-controls={`review-${item.id}`} tabIndex={tab === item.id ? 0 : -1} onClick={() => setTab(item.id)}>{item.label}</button>)}
    </div>
    <div className="rd-review-body">
      <div className="rd-review-main">
        <div id="review-diff" role="tabpanel" aria-labelledby="review-diff-tab" hidden={tab !== "diff"} className="rd-review-panel">
          {header && <DiffPane header={header} onAsk={ask} focus={focus} />}
          {!header && !error && <p className="rd-prs-empty" role="status">Loading {target}…</p>}
        </div>
        <div id="review-review" role="tabpanel" aria-labelledby="review-review-tab" hidden={tab !== "review"} className="rd-review-panel">
          {header && <CardsPane header={header} active={tab === "review"} openFile={openFile} ask={askQuestion} />}
        </div>
        <div id="review-history" role="tabpanel" aria-labelledby="review-history-tab" hidden={tab !== "history"} className="rd-review-panel">
          <p className="rd-prs-empty">Release history arrives in a later build step.</p>
        </div>
      </div>
      <aside className={`rd-review-chat${chat ? "" : " collapsed"}`} aria-label="Review chat">
        {chat && header && <ChatPanel header={header} quote={quote} onClearQuote={clearQuote} onOpenFile={(path, line) => openFile(path, undefined, line)}
          onDraft={body => setDialog({ event: "REQUEST_CHANGES", body })} onCollapse={() => setChat(false)} pendingQuestion={pendingQuestion} onQuestionTaken={questionTaken} />}
        {chat && !header && <p className="rd-prs-empty">Loading…</p>}
        {!chat && <button className="rd-review-chat-tab" onClick={() => setChat(true)}>Chat</button>}
      </aside>
    </div>
    {dialog && <Modal title={dialog.event === "APPROVE" ? "Approve on GitHub" : "Request changes on GitHub"} onClose={() => setDialog(null)}>
      <form className="rd-review-dialog" onSubmit={event => { event.preventDefault(); void post(); }}>
        <p className="rd-card-meta">Posts a {dialog.event === "APPROVE" ? "review approval" : "request-changes review"} on {pr ? `#${pr.number}` : "this PR"} as you. Nothing is sent until you click Post.</p>
        <textarea rows={10} aria-label="Review body" value={dialog.body} placeholder={dialog.event === "APPROVE" ? "Optional comment" : "What should change"} onChange={event => setDialog({ ...dialog, body: event.target.value })} />
        {dialogError && <p className="rd-prs-note" role="alert">{dialogError}</p>}
        <div className="rd-review-dialog-actions">
          <button type="button" className="rd-btn rd-btn-ghost" onClick={() => setDialog(null)}>Cancel</button>
          <button className="rd-btn rd-btn-primary" disabled={posting || (dialog.event !== "APPROVE" && !dialog.body.trim())}>{posting ? "Posting…" : dialog.event === "APPROVE" ? "Post approval" : "Post as review"}</button>
        </div>
      </form>
    </Modal>}
  </section>;
}
