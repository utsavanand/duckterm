import { useCallback, useEffect, useRef, useState } from "react";
import { api, type ChatMessage, type ReviewHeader } from "../api";
import { html } from "../render";

export function ChatPanel({ header, quote, onClearQuote, onOpenFile, onDraft, onCollapse, pendingQuestion, onQuestionTaken }: {
  header: ReviewHeader; quote: string; onClearQuote: () => void; onOpenFile: (path: string, line?: number) => void;
  onDraft: (body: string) => void; onCollapse: () => void; pendingQuestion: string; onQuestionTaken: () => void;
}) {
  const { repo, ref } = header;
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [suggestions, setSuggestions] = useState<string[]>([]);
  const [question, setQuestion] = useState("");
  const [pending, setPending] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [drafting, setDrafting] = useState(false);
  const log = useRef<HTMLDivElement>(null);
  const alive = useRef(true);
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);
  useEffect(() => {
    void api.reviewChat(repo, ref).then(result => { if (alive.current) { setMessages(result.messages); setSuggestions(result.suggestions); } })
      .catch((cause: Error) => { if (alive.current) setError(cause.message); });
  }, [repo, ref]);
  useEffect(() => { if (pendingQuestion) { setQuestion(pendingQuestion); onQuestionTaken(); } }, [pendingQuestion, onQuestionTaken]);
  useEffect(() => { if (log.current) log.current.scrollTop = log.current.scrollHeight; }, [messages, pending]);
  const ask = useCallback(async (text: string) => {
    const value = text.trim();
    if (!value || pending !== null) return;
    if (/draft (a )?request-changes/i.test(value)) { await draft(); return; }
    setPending(value); setError(""); setQuestion("");
    try {
      const entry = await api.reviewAsk(repo, ref, { question: value, hunk: quote || null });
      if (alive.current) { setMessages(previous => [...previous, entry]); onClearQuote(); }
    } catch (cause) {
      if (alive.current) { setError((cause as Error).message); setQuestion(value); }
    } finally {
      if (alive.current) setPending(null);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [repo, ref, quote, pending, onClearQuote]);
  async function draft() {
    setDrafting(true); setError("");
    try { const result = await api.reviewDraft(repo, ref); onDraft(result.body); }
    catch (cause) { setError((cause as Error).message); }
    finally { setDrafting(false); }
  }
  return <>
    <div className="rd-review-chat-head"><strong>Chat</strong><span>{header.pr ? `#${header.pr.number}` : header.branch}{header.session ? ` · with ${header.session.name}'s digest` : ""}</span><button aria-label="Collapse chat" onClick={onCollapse}>›</button></div>
    <div ref={log} className="rd-review-chat-body" aria-live="polite">
      {messages.length === 0 && pending === null && <p className="rd-prs-empty">Ask about this change. Answers cite path:line from the diff; click a citation to open it.</p>}
      {messages.map((message, index) => <div key={`${message.at}-${index}`} className="rd-chat-exchange">
        <div className="rd-chat-q"><small>You{message.hunk ? ` · about ${message.hunk}` : ""}</small><p>{message.q}</p></div>
        <div className="rd-chat-a"><div dangerouslySetInnerHTML={{ __html: html(message.a) }} />
          {message.citations.length > 0 && <p className="rd-chat-cites">{message.citations.map(cite => <button key={`${cite.path}:${cite.line}`} onClick={() => onOpenFile(cite.path, cite.line)}>{cite.path}:{cite.line}</button>)}</p>}
        </div>
      </div>)}
      {pending !== null && <div className="rd-chat-exchange"><div className="rd-chat-q"><small>You</small><p>{pending}</p></div><p role="status" className="rd-card-meta">Reading the diff…</p></div>}
      {error && <p className="rd-prs-note" role="alert">{error}</p>}
    </div>
    {quote && <blockquote className="rd-review-quote"><code>{quote}</code><button aria-label="Remove quote" onClick={onClearQuote}>×</button></blockquote>}
    <div className="rd-chat-suggestions">{suggestions.map(text => <button key={text} disabled={pending !== null || drafting} onClick={() => void ask(text)}>{text}</button>)}</div>
    <form className="rd-review-chat-compose" onSubmit={event => { event.preventDefault(); void ask(question); }}>
      <textarea rows={2} aria-label="Ask about this pull request" placeholder="Ask about this pull request…" value={question} disabled={pending !== null} onChange={event => setQuestion(event.target.value)} onKeyDown={event => { if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) { event.preventDefault(); void ask(question); } }} />
      <button className="rd-btn rd-btn-primary rd-btn-sm" disabled={pending !== null || !question.trim()}>Ask</button>
    </form>
  </>;
}
