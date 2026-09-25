import { useEffect, useRef, useState } from "react";
import { api, OracleExchange } from "./api";
import { html } from "./render";

// Only questions the digest can answer: it carries each session's state,
// goal, context size, last checkpoint, and recent screen, but no inbox data.
const SUGGESTIONS = [
  "Which sessions are waiting on me?",
  "What is each session working on right now?",
  "Which sessions are running low on context?",
  "Are any two sessions working on the same thing?",
];

// A docked chat with Oracle: answers come from /fleet/ask (one summarizer
// call over a digest of every running session). The server stores the
// conversation, so it survives reloads and is shared by the browser and the
// Mac app; it also supplies the last exchanges as follow-up context.
export function OracleChat({ onClose }: { onClose: () => void }) {
  const [log, setLog] = useState<OracleExchange[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [q, setQ] = useState("");
  const [pending, setPending] = useState<string | null>(null);
  const [error, setError] = useState("");
  const bottom = useRef<HTMLDivElement>(null);
  const input = useRef<HTMLTextAreaElement>(null);

  // Grow with the text up to the CSS max-height, then scroll.
  useEffect(() => {
    const el = input.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${el.scrollHeight}px`;
  }, [q]);

  useEffect(() => {
    let stale = false;
    api
      .oracleChat()
      .then((r) => { if (!stale) setLog(r.messages); })
      .catch((e: Error) => { if (!stale) setError(`Could not load the conversation: ${e.message}`); })
      .finally(() => { if (!stale) setLoaded(true); });
    return () => { stale = true; };
  }, []);

  useEffect(() => {
    bottom.current?.scrollIntoView?.({ block: "end" });
  }, [log, pending, error]);

  async function ask(text: string = q) {
    const question = text.trim();
    if (!question || pending !== null) return;
    setPending(question);
    setQ("");
    setError("");
    try {
      const r = await api.fleetAsk(question);
      setLog((l) => [...l, r.exchange]);
    } catch (e) {
      setError(`Oracle could not answer: ${(e as Error).message}`);
      setQ(question);
    } finally {
      setPending(null);
    }
  }

  async function clear() {
    try {
      setLog((await api.clearOracleChat()).messages);
    } catch (e) {
      setError(`Could not clear: ${(e as Error).message}`);
    }
  }

  return (
    <aside className="rd-oracle" aria-label="Oracle chat">
      <header className="rd-oracle-head">
        <span className="rd-oracle-title">Oracle</span>
        <span className="rd-spacer" />
        {log.length > 0 && (
          <button className="rd-btn rd-btn-ghost rd-btn-sm" onClick={() => void clear()}>
            Clear
          </button>
        )}
        <button
          className="rd-btn rd-btn-ghost rd-btn-sm"
          onClick={onClose}
          aria-label="Close Oracle"
        >
          ✕
        </button>
      </header>
      <div className="rd-oracle-log">
        {loaded && log.length === 0 && pending === null && (
          <div className="rd-oracle-empty">
            <p>
              Ask about all your running sessions at once. Oracle reads each
              session's state, goal, context size, and recent terminal output.
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
        {log.map((x, i) => (
          <div key={`${x.at}-${i}`} className="rd-oracle-exchange">
            <div className="rd-oracle-q">{x.q}</div>
            <div
              className="rd-oracle-a rd-msg-text"
              dangerouslySetInnerHTML={{ __html: html(x.a) }}
            />
          </div>
        ))}
        {pending !== null && (
          <div className="rd-oracle-exchange">
            <div className="rd-oracle-q">{pending}</div>
            <div className="rd-oracle-thinking" role="status">
              <span className="rd-spinner" /> Reading your sessions…
            </div>
          </div>
        )}
        {error && <p className="rd-oracle-error" role="alert">{error}</p>}
        <div ref={bottom} />
      </div>
      <div className="rd-oracle-compose">
        <textarea
          ref={input}
          aria-label="Message Oracle"
          value={q}
          rows={1}
          placeholder="Ask about your running sessions"
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
  );
}
