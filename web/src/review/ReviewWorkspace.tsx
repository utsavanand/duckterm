import { useCallback, useEffect, useRef, useState } from "react";
import { api, type ReviewHeader } from "../api";
import { DiffPane } from "./DiffPane";
import { shortSha } from "./diffModel";
import "./review.css";

type Tab = "diff" | "review" | "history";
const TABS: { id: Tab; label: string }[] = [{ id: "diff", label: "Diff" }, { id: "review", label: "Review" }, { id: "history", label: "History" }];

export function ReviewWorkspace({ repo, target, from, onBack }: { repo: string; target: string; from?: string; onBack: () => void }) {
  const [header, setHeader] = useState<ReviewHeader | null>(null);
  const [error, setError] = useState("");
  const [tab, setTab] = useState<Tab>("diff");
  const [chat, setChat] = useState(() => window.innerWidth >= 1000);
  const [quote, setQuote] = useState("");
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
  function ask(text: string) { setQuote(text); setChat(true); }
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
          {from && ` · from ${from}`}
          {` · head ${shortSha(header.head_sha)}`}
        </p>}
        {error && <p className="rd-prs-note" role="alert">{error}</p>}
      </div>
      <div className="rd-review-actions">
        <button className="rd-btn rd-btn-ghost rd-btn-sm" aria-pressed={chat} onClick={() => setChat(open => !open)}>Chat</button>
        {github && <a className="rd-prs-link" href={github} target="_blank" rel="noreferrer">Open on GitHub</a>}
      </div>
    </header>
    <div className="rd-review-tabs" role="tablist" aria-label="Review views">
      {TABS.map(item => <button key={item.id} id={`review-${item.id}-tab`} role="tab" aria-selected={tab === item.id} aria-controls={`review-${item.id}`} tabIndex={tab === item.id ? 0 : -1} onClick={() => setTab(item.id)}>{item.label}</button>)}
    </div>
    <div className="rd-review-body">
      <div className="rd-review-main">
        <div id="review-diff" role="tabpanel" aria-labelledby="review-diff-tab" hidden={tab !== "diff"} className="rd-review-panel">
          {header && <DiffPane header={header} onAsk={ask} />}
          {!header && !error && <p className="rd-prs-empty" role="status">Loading {target}…</p>}
        </div>
        <div id="review-review" role="tabpanel" aria-labelledby="review-review-tab" hidden={tab !== "review"} className="rd-review-panel">
          <p className="rd-prs-empty">The ten review cards arrive in the next build step. For now, the Diff tab orders files by risk and remembers what you have viewed.</p>
        </div>
        <div id="review-history" role="tabpanel" aria-labelledby="review-history-tab" hidden={tab !== "history"} className="rd-review-panel">
          <p className="rd-prs-empty">Release history arrives in a later build step.</p>
        </div>
      </div>
      <aside className={`rd-review-chat${chat ? "" : " collapsed"}`} aria-label="Review chat">
        {chat ? <>
          <div className="rd-review-chat-head"><strong>Chat</strong><span>{pr ? `#${pr.number}` : header?.branch ?? target}</span><button aria-label="Collapse chat" onClick={() => setChat(false)}>›</button></div>
          <div className="rd-review-chat-body">
            <p className="rd-prs-empty">Questions about this change are answered here once the review cards land. Until then, use Ask about this hunk to collect what you want to ask.</p>
            {quote && <blockquote className="rd-review-quote"><code>{quote}</code><button aria-label="Remove quote" onClick={() => setQuote("")}>×</button></blockquote>}
          </div>
          <form className="rd-review-chat-compose" onSubmit={event => event.preventDefault()}>
            <textarea rows={2} aria-label="Ask about this pull request" placeholder="Ask about this pull request…" disabled />
            <button className="rd-btn rd-btn-primary rd-btn-sm" disabled>Ask</button>
          </form>
        </> : <button className="rd-review-chat-tab" onClick={() => setChat(true)}>Chat</button>}
      </aside>
    </div>
  </section>;
}
