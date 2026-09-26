import { useEffect, useRef, useState } from "react";
import { api, OracleExchange, RelayNote, RelayRule, RelayState } from "./api";
import { html } from "./render";
import { NoteCard, ProposalCard, RuleFields } from "./RelayNotes";

// Chat-only entries: rule proposals and short replies to rule commands.
type Local =
  | { at: number; kind: "proposal"; rule: RelayRule; state: "open" | "created" | "cancelled"; id?: string }
  | { at: number; kind: "info"; text: string; rules?: RelayRule[] };

type Entry =
  | { at: number; type: "exchange"; exchange: OracleExchange }
  | { at: number; type: "note"; note: RelayNote }
  | { at: number; type: "local"; local: Local; index: number };

// "always …", "never …", "when an agent asks …": a rule, not a question.
const RULE_WORDS = /^(always|never|when|whenever|if an agent|if agents)\b/i;

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
export function OracleChat({
  relay,
  onRelayChange,
  onClose,
}: {
  relay: RelayState;
  onRelayChange: () => void;
  onClose?: () => void;
}) {
  const [locals, setLocals] = useState<Local[]>([]);
  const [log, setLog] = useState<OracleExchange[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [q, setQ] = useState("");
  const [pending, setPending] = useState<string | null>(null);
  const [error, setError] = useState("");
  const logRef = useRef<HTMLDivElement>(null);
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
    // Scroll only the message list. scrollIntoView also scrolled every
    // scrollable ancestor, which pushed the control tower's header off screen.
    const log = logRef.current;
    if (log) log.scrollTop = log.scrollHeight;
  }, [log, pending, error, locals.length, relay.notes.length]);

  const addLocal = (l: Local) => setLocals((ls) => [...ls, l]);

  async function propose(text: string) {
    setPending(text);
    setError("");
    try {
      const { rule } = await api.proposeRule(text);
      addLocal({ at: Date.now(), kind: "proposal", rule, state: "open" });
    } catch (e) {
      addLocal({ at: Date.now(), kind: "info", text: (e as Error).message });
    } finally {
      setPending(null);
    }
  }

  async function send(text: string = q) {
    const t = text.trim();
    if (!t || pending !== null) return;
    setQ("");
    const stop = t.match(/^stop\s+(r\d+)$/i);
    if (stop) {
      try {
        await api.deleteRule(stop[1].toUpperCase());
        addLocal({ at: Date.now(), kind: "info", text: `Stopped ${stop[1].toUpperCase()}.` });
      } catch (e) {
        addLocal({ at: Date.now(), kind: "info", text: (e as Error).message });
      }
      onRelayChange();
      return;
    }
    if (/^rules$/i.test(t)) {
      addLocal({ at: Date.now(), kind: "info", text: relay.rules.length ? "Your rules:" : "No rules yet. Start one with \"always …\" or \"when an agent asks …\".", rules: relay.rules });
      return;
    }
    if (RULE_WORDS.test(t)) return propose(t);
    return ask(t);
  }

  const entries: Entry[] = [
    ...log.map((exchange) => ({ at: exchange.at, type: "exchange" as const, exchange })),
    ...relay.notes.map((note) => ({ at: note.created_at, type: "note" as const, note })),
    ...locals.map((local, index) => ({ at: local.at, type: "local" as const, local, index })),
  ].sort((a, b) => a.at - b.at);

  function setLocal(index: number, patch: Partial<Local>) {
    setLocals((ls) => ls.map((l, i) => (i === index ? ({ ...l, ...patch } as Local) : l)));
  }

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
        <span className="rd-oracle-title">Ask Oracle</span>
        {relay.open > 0 && <span className="rd-relay-count">{relay.open} need{relay.open === 1 ? "s" : ""} you</span>}
        <span className="rd-spacer" />
        {log.length > 0 && (
          <button className="rd-btn rd-btn-ghost rd-btn-sm" onClick={() => void clear()}>
            Clear
          </button>
        )}
        {onClose && (
          <button
            className="rd-btn rd-btn-ghost rd-btn-sm"
            onClick={onClose}
            aria-label="Close Oracle"
          >
            ✕
          </button>
        )}
      </header>
      <div className="rd-oracle-log" ref={logRef}>
        {loaded && entries.length === 0 && pending === null && (
          <div className="rd-oracle-empty">
            <p>
              Ask about all your running sessions at once. Oracle reads each
              session's state, goal, context size, and recent terminal output.
              When an agent needs you, its question shows up here to answer.
            </p>
            <div className="rd-oracle-suggestions">
              {SUGGESTIONS.map((s) => (
                <button key={s} className="rd-oracle-suggestion" onClick={() => void send(s)}>
                  {s}
                </button>
              ))}
            </div>
          </div>
        )}
        {entries.map((e) =>
          e.type === "exchange" ? (
            <div key={`x-${e.at}`} className="rd-oracle-exchange">
              <div className="rd-oracle-q">{e.exchange.q}</div>
              <div
                className="rd-oracle-a rd-msg-text"
                dangerouslySetInnerHTML={{ __html: html(e.exchange.a) }}
              />
            </div>
          ) : e.type === "note" ? (
            <NoteCard key={e.note.id} note={e.note} rules={relay.rules} onChange={onRelayChange} onPropose={(t) => void propose(t)} />
          ) : e.local.kind === "proposal" ? (
            e.local.state === "open" ? (
              <ProposalCard
                key={`l-${e.index}`}
                rule={e.local.rule}
                onCreated={(rule) => { setLocal(e.index, { state: "created", id: rule.id }); onRelayChange(); }}
                onCancel={() => setLocal(e.index, { state: "cancelled" })}
              />
            ) : (
              <p key={`l-${e.index}`} className="rd-relay-status">
                {e.local.state === "created"
                  ? `Created ${e.local.id}: ${e.local.rule.summary || "rule"}. Say "stop ${e.local.id}" to turn it off.`
                  : "Rule not created."}
              </p>
            )
          ) : (
            <div key={`l-${e.index}`} className="rd-relay-info">
              <p>{e.local.text}</p>
              {e.local.rules?.map((r) => (
                <div key={r.id} className="rd-relay-rule">
                  <span className="rd-relay-kind rule">{r.id}</span>
                  <RuleFields rule={r} />
                </div>
              ))}
            </div>
          ),
        )}
        {pending !== null && (
          <div className="rd-oracle-exchange">
            <div className="rd-oracle-q">{pending}</div>
            <div className="rd-oracle-thinking" role="status">
              <span className="rd-spinner" /> Reading your sessions…
            </div>
          </div>
        )}
        {error && <p className="rd-oracle-error" role="alert">{error}</p>}
      </div>
      <div className="rd-oracle-compose">
        <textarea
          ref={input}
          aria-label="Message Oracle"
          value={q}
          rows={1}
          placeholder="Answer a note, ask about your sessions, or make a rule"
          onChange={(e) => setQ(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
              e.preventDefault();
              void send();
            }
          }}
        />
        <button
          className="rd-btn rd-btn-primary rd-btn-sm"
          disabled={pending !== null || !q.trim()}
          onClick={() => void send()}
        >
          Send
        </button>
      </div>
    </aside>
  );
}
