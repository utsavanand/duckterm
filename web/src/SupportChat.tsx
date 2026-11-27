import { ChatProgress, useChatProgress } from "./ChatProgress";
import { useEffect, useRef, useState } from "react";
import { api, OracleExchange } from "./api";
import { html } from "./render";
import "./support.css";

const SUGGESTIONS = [
  "My session isn't showing in the sidebar. What do I check?",
  "How do agents message each other?",
  "How do I update DuckTerm?",
  "What does Oracle do when an agent is idle?",
];

// Answers come from /support/ask: one model call over the bundled user guide
// and this instance's doctor results. Styles are shared with the Oracle chat.
export function SupportChat() {
  const [log, setLog] = useState<OracleExchange[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [q, setQ] = useState("");
  const progress = useChatProgress();
  const [pending, setPending] = useState<string | null>(null);
  const [error, setError] = useState("");
  const logRef = useRef<HTMLDivElement>(null);
  const input = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    const el = input.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${el.scrollHeight}px`;
  }, [q]);

  useEffect(() => {
    let stale = false;
    api
      .supportChat()
      .then((r) => { if (!stale) setLog(r.messages); })
      .catch((e: Error) => { if (!stale) setError(`Could not load the conversation: ${e.message}`); })
      .finally(() => { if (!stale) setLoaded(true); });
    return () => { stale = true; };
  }, []);

  useEffect(() => {
    const el = logRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [log, pending, error]);

  async function ask(text: string = q) {
    const question = text.trim();
    if (!question || pending !== null) return;
    setPending(question);
    setQ("");
    setError("");
    try {
      const r = await progress.run(id => api.supportAsk(question, id));
      setLog((l) => [...l, r.exchange]);
    } catch (e) {
      setError(`Support could not answer: ${(e as Error).message}`);
      setQ(question);
    } finally {
      setPending(null);
    }
  }

  async function clear() {
    try {
      setLog((await api.clearSupportChat()).messages);
    } catch (e) {
      setError(`Could not clear: ${(e as Error).message}`);
    }
  }

  return (
    <div className="rd-support-page">
      <aside className="rd-oracle rd-support" aria-label="Support chat">
        <header className="rd-oracle-head">
          <span className="rd-oracle-title">Support</span>
          <span className="rd-spacer" />
          {log.length > 0 && (
            <button className="rd-btn rd-btn-ghost rd-btn-sm" onClick={() => void clear()}>
              Clear
            </button>
          )}
        </header>
        <div className="rd-oracle-log" ref={logRef}>
          {loaded && log.length === 0 && pending === null && (
            <div className="rd-oracle-empty">
              <p>
                Ask how to use DuckTerm or how to fix something. Support answers
                from the DuckTerm guide and checks this Mac's install (hooks,
                jq, curl, sign-in token) with each question.
              </p>
              <div className="rd-oracle-suggestions">
                {SUGGESTIONS.map((s) => (
                  <button key={s} className="rd-oracle-suggestion" onClick={() => void ask(s)}>
                    {s}
                  </button>
                ))}
              </div>
            </div>
          )}
          {log.map((x) => (
            <div key={`x-${x.at}`} className="rd-oracle-exchange">
              <div className="rd-oracle-q">{x.q}</div>
              <div className="rd-oracle-a rd-msg-text" dangerouslySetInnerHTML={{ __html: html(x.a) }} /><ChatProgress value={x.progress} />
            </div>
          ))}
          {pending !== null && (
            <div className="rd-oracle-exchange">
              <div className="rd-oracle-q">{pending}</div>
              <ChatProgress value={progress.value} startedAt={progress.startedAt} />
            </div>
          )}
          {!pending && progress.value?.state === "failed" && <ChatProgress value={progress.value} />}
      {error && <p className="rd-oracle-error" role="alert">{error}</p>}
        </div>
        <div className="rd-oracle-compose">
          <textarea
            ref={input}
            aria-label="Message Support"
            value={q}
            rows={1}
            placeholder="Ask how something works or what's wrong"
            onChange={(e) => setQ(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
                e.preventDefault();
                void ask();
              }
            }}
          />
          <button
            className="rd-btn rd-btn-primary rd-btn-sm"
            disabled={pending !== null || !q.trim()}
            onClick={() => void ask()}
          >
            Send
          </button>
        </div>
      </aside>
    </div>
  );
}
